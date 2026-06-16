"""PentaLoom 主入口.

包装 ClaudeSDKClient + ClaudeAgentOptions, 注入:
  - SQLiteSessionStore (entries/summaries 镜像到 alembic 管的 DB)
  - 主 agent tools 全集
  - Novita API key/base_url (走 SDK 子进程 env, 不污染父进程)

用法:
    async with PentaLoom() as pl:
        async for msg in pl.query("帮我看看 tests 目录"):
            ...
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
)

from pentaloom.capabilities.browser import compute_session_name
from pentaloom.config import get_settings
from pentaloom.infra import SQLiteSessionStore
from pentaloom.prompts import assemble_main_prompt
from pentaloom.prompts.skills import ENABLED_SKILLS
from pentaloom.tools import (
    ALL_WEAVER_FULL_NAMES,
    FILE_READ_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
    FILES_MCP_SERVER,
    FILES_MCP_SERVER_NAME,
    HITL_TOOL_NAMES,
    INSTALL_LIBS_FULL_NAME,
    INSTALL_NOTO_SANS_SC_FULL_NAME,
    PYTHON_ENV_MCP_SERVER,
    PYTHON_ENV_MCP_SERVER_NAME,
    REQUEST_WORKSPACE_DIR_TOOL_NAME,
    RUN_SCRIPT_FULL_NAME,
    SEARCH_MCP_SERVER,
    SEARCH_MCP_SERVER_NAME,
    WEB_SEARCH_FULL_NAME,
    WORKSPACE_MCP_SERVER,
    WORKSPACE_MCP_SERVER_NAME,
    build_hitl_hooks,
)
from pentaloom.tools.browser import (
    BROWSER_MCP_SERVER_NAME,
    BROWSER_SESSION_INFO_FULL_NAME,
    BROWSER_USE_FULL_NAME,
    INSTALL_BROWSER_USE_FULL_NAME,
    build_browser_mcp_server,
)
from pentaloom.tools.browser_bridge import (
    BROWSER_BRIDGE_FULL_NAME,
    BROWSER_BRIDGE_MCP_SERVER,
    BROWSER_BRIDGE_MCP_SERVER_NAME,
)
from pentaloom.tools.computer_use import (
    COMPUTER_MCP_SERVER,
    COMPUTER_MCP_SERVER_NAME,
    COMPUTER_USE_FULL_NAME,
)

# 主 agent 的工具全集. subagent 的 tools 只能从这里挑.
# Task 必带 — 派 subagent 全靠它.
# mcp__<server>__<tool> 是 in-process MCP 工具的完整名, 必须显式列在 tools 里,
# SDK 才会暴露给 LLM.
DEFAULT_TOOLS: list[str] = [
    "Task",
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "TodoWrite",
    # Skill 必须显式列在 tools — SDK 的 _apply_skills_defaults 只把 Skill(<name>)
    # 加进 allowed_tools 当 permission rule, 不会自动注册 Skill 工具本身. 不加
    # 这条 LLM 拿不到 Skill 工具, 只能 ls/find 去翻 .claude/skills/ 找 SKILL.md
    # 自己读 — 实测过, 跟 SDK 原生 skill 加载语义对不上.
    "Skill",
    # SDK / CLI 内置 WebFetch — 拉单个 URL 跑小模型抽信息. 跟 web_search 配合:
    # web_search 找链接, WebFetch 读完整页. browser_bridge 留给需要登录/JS/截图.
    "WebFetch",
    REQUEST_WORKSPACE_DIR_TOOL_NAME,
    INSTALL_LIBS_FULL_NAME,
    INSTALL_NOTO_SANS_SC_FULL_NAME,
    RUN_SCRIPT_FULL_NAME,
    FILE_READ_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
    INSTALL_BROWSER_USE_FULL_NAME,
    BROWSER_USE_FULL_NAME,
    BROWSER_SESSION_INFO_FULL_NAME,
    BROWSER_BRIDGE_FULL_NAME,
    COMPUTER_USE_FULL_NAME,
    WEB_SEARCH_FULL_NAME,
    # weaver 7 个工具 (1 weave_skill + 6 meta-tool). 工具 server 在 LoomPool._build
    # 时 per-session 构造 (因为要捕获 sid 回调 mark_pending_rebuild), 不在 app.py
    # 模块顶层 singleton — 跟 BROWSER_MCP_SERVER 同款 per-session 模式.
    *ALL_WEAVER_FULL_NAMES,
]

# 不需要 prompt 的工具 (auto-approve). HITL 工具 (Bash + request_workspace_dir)
# 必须走 can_use_tool 拿 tool_use_id 跟前端按钮 / 弹窗对齐, 所以这里特意剔除.
DEFAULT_ALLOWED_TOOLS: list[str] = [t for t in DEFAULT_TOOLS if t not in HITL_TOOL_NAMES]

class PentaLoom:
    def __init__(
        self,
        *,
        system_prompt: str | None = None,
        agents: dict[str, AgentDefinition] | None = None,
        extra_tools: list[str] | None = None,
        extra_mcp_servers: dict[str, Any] | None = None,
        extra_agents: dict[str, AgentDefinition] | None = None,
        extra_skills: list[str] | None = None,
        session_id: str | None = None,
        resume: str | None = None,
        cwd: str | Path | None = None,
        add_dirs: list[str | Path] | None = None,
        can_use_tool: Any = None,
    ) -> None:
        self._settings = get_settings()
        self._store = SQLiteSessionStore()
        self._client: ClaudeSDKClient | None = None

        tools = list(DEFAULT_TOOLS)
        if extra_tools:
            tools.extend(t for t in extra_tools if t not in tools)
        # extra_tools 默认进 auto-approve (避免用户加自定义工具还要手动配 allow).
        # HITL 工具 (Bash + request_workspace_dir + browser_*) 保留 prompt — 都是策略点,
        # 必须走 can_use_tool.
        allowed = [t for t in tools if t not in HITL_TOOL_NAMES]

        # browser MCP server 是 per-instance 工厂构造的 — session_name 跟 sandbox
        # 闭包在工具体内, LLM 调工具时不用 (也无法) 传 session id. 没 sid 时回退默认值,
        # 给程序化直起 PentaLoom (不走 LoomPool) 也能正常加载 browser server.
        logical_sid = session_id or resume or "default"
        browser_sandbox = Path(cwd) if cwd else (self._settings.data_dir / "default-sandbox")
        browser_server = build_browser_mcp_server(
            compute_session_name(logical_sid), browser_sandbox
        )

        # 显式传 system_prompt 时直接用 (调用方负责完整性); 否则按四段式组装.
        # ENABLED_SKILLS 空列表时把 skills= 留 None, 不覆盖 SDK 默认.
        resolved_prompt = (
            system_prompt
            if system_prompt is not None
            else assemble_main_prompt(mounted_dirs=add_dirs)
        )

        # 内置 + extra (e.g., LoomPool 注入的 weaver) 合并. extra 不能覆盖内置.
        merged_mcp_servers: dict[str, Any] = {
            WORKSPACE_MCP_SERVER_NAME: WORKSPACE_MCP_SERVER,
            PYTHON_ENV_MCP_SERVER_NAME: PYTHON_ENV_MCP_SERVER,
            FILES_MCP_SERVER_NAME: FILES_MCP_SERVER,
            BROWSER_MCP_SERVER_NAME: browser_server,
            # bridge 是 user-scoped (一个用户 Chrome 通常就一个), 模块级 singleton
            # 跨 PentaLoom session 共享同一组扩展连接.
            BROWSER_BRIDGE_MCP_SERVER_NAME: BROWSER_BRIDGE_MCP_SERVER,
            # computer-use 也 user-scoped (一台机器), 模块级 singleton.
            COMPUTER_MCP_SERVER_NAME: COMPUTER_MCP_SERVER,
            # search 是 stateless HTTP 调用, 模块级 singleton 即可.
            SEARCH_MCP_SERVER_NAME: SEARCH_MCP_SERVER,
        }
        if extra_mcp_servers:
            for k, v in extra_mcp_servers.items():
                if k in merged_mcp_servers:
                    raise ValueError(f"extra_mcp_servers key {k!r} 跟内置同名")
                merged_mcp_servers[k] = v

        merged_agents = {**(agents or {}), **(extra_agents or {})}

        # ENABLED_SKILLS (内置) + extra_skills (weaver 织的) 合并; 空 list 退化 None.
        all_skill_names = list(ENABLED_SKILLS) + list(extra_skills or [])
        skills_for_options = all_skill_names if all_skill_names else None

        self._options = ClaudeAgentOptions(
            model=self._settings.model,
            tools=tools,
            allowed_tools=allowed,
            agents=merged_agents,
            system_prompt=resolved_prompt,
            skills=skills_for_options,
            # "project" → CLI 从 cwd (= sandbox 目录) 往上找 .claude/, 命中
            # repo-root .claude/skills/<name>/SKILL.md (内置) +
            # data_dir/.claude/skills/<name>/ (weaver symlink).
            # 不读 user 全局 .claude/, 避免跟用户自己的 Claude Code 配置串味.
            # 关键: skills= 传 list 时, SDK 的 _apply_skills_defaults 只在
            # setting_sources is None 时才默认填 ["user","project"]; 显式 [] 会
            # 跳过自动填, 导致 CLI 根本不扫 skill 目录 — 必须显式设 project.
            setting_sources=["project"],
            strict_mcp_config=True,
            extra_args={},  # 不加 --bare, 否则 Task 工具会被压掉
            session_store=self._store,
            session_id=session_id,
            resume=resume,
            cwd=cwd,
            add_dirs=list(add_dirs) if add_dirs else [],
            mcp_servers=merged_mcp_servers,
            can_use_tool=can_use_tool,
            # PreToolUse hook 把 Bash 标成 "ask" 路由到 can_use_tool. SDK 文档里
            # can_use_tool "not invoked for tool calls already permitted by
            # allowed_tools / permission_mode / settings rules" — CLI 对 Bash 有
            # 内建放行, 必须显式 "ask" 才会触发 can_use_tool. 见 tools/workspace.py.
            hooks=build_hitl_hooks(),
            env={
                "ANTHROPIC_API_KEY": self._settings.anthropic_api_key,
                "ANTHROPIC_BASE_URL": self._settings.anthropic_base_url,
            },
            # 开 token 级 partial 帧 — 没这个, SDK 只在整条 message 成型后推
            # 一次, 前端虽走 SSE 但看起来是"刷一段刷一段", 完全没有打字机效果.
            include_partial_messages=True,
            # Opus 4.7+ 默认 display="omitted" — thinking 字段只有 signature, 没明文.
            # 要展示给用户看, 显式开 summarized (SDK types.py:1555-1557 注释).
            thinking={"type": "adaptive", "display": "summarized"},
        )

    async def __aenter__(self) -> "PentaLoom":
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def query(self, prompt: str) -> AsyncIterator[Any]:
        if self._client is None:
            raise RuntimeError(
                "PentaLoom not started; use `async with PentaLoom() as pl:`"
            )
        await self._client.query(prompt)
        async for msg in self._client.receive_response():
            yield msg

    async def query_multimodal(
        self, content_blocks: list[dict[str, Any]]
    ) -> AsyncIterator[Any]:
        """走 content blocks list 的 user message — 给 inline image 这种多模态用.

        SDK 的 client.query() 接 str 时直接 transport.write(json), 接 AsyncIterable
        时走 stream_input 会 wait_for_result_and_end_input 关 stdin (streaming 语义).
        我们的 PentaLoom 是 long-lived client 跑多轮 turn, stream_input 关 stdin 后
        第二轮就废. 所以这里复制 SDK str 模式的逻辑, 只是把 content 字段从 str
        换成 list[dict] (Anthropic content blocks 格式, 含 image / text / 等).

        访问私有 _transport 是有意的 — SDK 当前没暴露 "single-shot dict prompt"
        公共 API. 升级 SDK 时验证 client._transport 仍是 SubprocessCLITransport
        且 .write() 协议不变.
        """
        if self._client is None:
            raise RuntimeError(
                "PentaLoom not started; use `async with PentaLoom() as pl:`"
            )
        message = {
            "type": "user",
            "message": {"role": "user", "content": content_blocks},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
        await self._client._transport.write(json.dumps(message) + "\n")
        async for msg in self._client.receive_response():
            yield msg

    @property
    def client(self) -> ClaudeSDKClient:
        """暴露底层 client, 给高阶用法 (stop_task 等) 用."""
        if self._client is None:
            raise RuntimeError("PentaLoom not started")
        return self._client
