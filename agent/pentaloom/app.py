"""PentaLoom 主入口.

包装 ClaudeSDKClient + ClaudeAgentOptions, 注入:
  - SQLiteSessionStore (entries/summaries 镜像到 alembic 管的 DB)
  - 主 agent tools 全集 (subagent tools 必须是子集, 见 sdk-探索结论)
  - Novita API key/base_url (走 SDK 子进程 env, 不污染父进程)

用法:
    from pentaloom import PentaLoom

    async with PentaLoom() as pl:
        async for msg in pl.query("帮我看看 tests 目录"):
            ...
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
)

from pentaloom.config import get_settings
from pentaloom.infra import SQLiteSessionStore
from pentaloom.prompts import assemble_main_prompt
from pentaloom.prompts.skills import ENABLED_SKILLS
from pentaloom.tools import (
    FILE_READ_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
    FILES_MCP_SERVER,
    FILES_MCP_SERVER_NAME,
    HITL_TOOL_NAMES,
    INSTALL_LIBS_FULL_NAME,
    PYTHON_ENV_MCP_SERVER,
    PYTHON_ENV_MCP_SERVER_NAME,
    REQUEST_WORKSPACE_DIR_TOOL_NAME,
    RUN_SCRIPT_FULL_NAME,
    WORKSPACE_MCP_SERVER,
    WORKSPACE_MCP_SERVER_NAME,
    build_hitl_hooks,
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
    REQUEST_WORKSPACE_DIR_TOOL_NAME,
    INSTALL_LIBS_FULL_NAME,
    RUN_SCRIPT_FULL_NAME,
    FILE_READ_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
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
        # HITL 工具 (Bash + request_workspace_dir) 保留 prompt — 都是策略点,
        # 必须走 can_use_tool.
        allowed = [t for t in tools if t not in HITL_TOOL_NAMES]

        # 显式传 system_prompt 时直接用 (调用方负责完整性); 否则按四段式组装.
        # ENABLED_SKILLS 空列表时把 skills= 留 None, 不覆盖 SDK 默认.
        resolved_prompt = (
            system_prompt
            if system_prompt is not None
            else assemble_main_prompt(mounted_dirs=add_dirs)
        )
        self._options = ClaudeAgentOptions(
            model=self._settings.model,
            tools=tools,
            allowed_tools=allowed,
            agents=agents or {},
            system_prompt=resolved_prompt,
            skills=list(ENABLED_SKILLS) if ENABLED_SKILLS else None,
            setting_sources=[],
            strict_mcp_config=True,
            extra_args={},  # 不加 --bare, 否则 Task 工具会被压掉
            session_store=self._store,
            session_id=session_id,
            resume=resume,
            cwd=cwd,
            add_dirs=list(add_dirs) if add_dirs else [],
            mcp_servers={
                WORKSPACE_MCP_SERVER_NAME: WORKSPACE_MCP_SERVER,
                PYTHON_ENV_MCP_SERVER_NAME: PYTHON_ENV_MCP_SERVER,
                FILES_MCP_SERVER_NAME: FILES_MCP_SERVER,
            },
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

    @property
    def client(self) -> ClaudeSDKClient:
        """暴露底层 client, 给高阶用法 (stop_task 等) 用."""
        if self._client is None:
            raise RuntimeError("PentaLoom not started")
        return self._client
