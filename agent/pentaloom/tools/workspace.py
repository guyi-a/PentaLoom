"""权限审批中枢 + workspace 动态挂载工具.

文件叫 workspace 是历史原因 (最早只有这个工具). 现在它也充当通用 HITL 路由:
任何需要"用户拍板"的工具调用都在 make_can_use_tool 里注册一个 Future,
通过 SSE 的 tool_use 帧推到前端, 前端 POST /chat/permission/{tool_use_id}
回执后 Future 完成, can_use_tool 才返回 Allow/Deny.

当前接入审批的工具:
  - mcp__pentaloom__request_workspace_dir: 弹模态. allow_session 退化为 allow_once.
  - Bash: 内联在 ToolUseBlock 卡片下三按钮. allow_session = 把这条命令串加入
    本会话 Bash 白名单 (set[str]), 下次同样命令免审. 白名单跟 LoomPool 的
    _Entry 共生命周期, evict / 重启都清空.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
    create_sdk_mcp_server,
    tool,
)
from loguru import logger

from pentaloom.tools.files import (
    FILE_READ_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
    FILES_MCP_SERVER,
    FILES_MCP_SERVER_NAME,
)
from pentaloom.tools.python_env import (
    INSTALL_LIBS_FULL_NAME,
    PYTHON_ENV_MCP_SERVER_NAME,
    PYTHON_ENV_TOOLS,
    RUN_SCRIPT_FULL_NAME,
)
from pentaloom.tools.system_resources import (
    INSTALL_NOTO_SANS_SC_FULL_NAME,
    SYSTEM_RESOURCES_TOOLS,
)

WORKSPACE_MCP_SERVER_NAME = "pentaloom"
REQUEST_TOOL_NAME = "request_workspace_dir"
FULL_TOOL_NAME = f"mcp__{WORKSPACE_MCP_SERVER_NAME}__{REQUEST_TOOL_NAME}"


# 主 prompt 的"工具守则"段会拼这一段.
WORKSPACE_PROMPT_INSTRUCTIONS: str = (
    "### 工作区挂载\n"
    "- 用户提到本地目录 / 项目, 但当前没在已授权目录里时, 调 "
    f"{FULL_TOOL_NAME} 请求挂载, 别自己猜路径或用 Bash 直接读.\n"
    "- 挂载在用户同意的下一轮 turn 才生效, 当前 turn 内仍是旧的文件系统权限."
)

# Bash 在 app.py 的 allowed_tools 里被剔除 → 每次调用都会触发 can_use_tool.
BASH_TOOL_NAME = "Bash"

# 这些名字以外的工具都自动放行. 列在这里方便前端用相同集合判断"哪些 tool_use 帧
# 该渲染审批按钮". 若新增需要 HITL 的工具, 改这里 + 前端 TOOLS_NEEDING_APPROVAL.
# file_verify 也在这里, 但 can_use_tool 内会按 input.autofix 判断: True 才审, False 直放.
HITL_TOOL_NAMES: frozenset[str] = frozenset({
    FULL_TOOL_NAME,
    BASH_TOOL_NAME,
    INSTALL_LIBS_FULL_NAME,
    RUN_SCRIPT_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
    INSTALL_NOTO_SANS_SC_FULL_NAME,
})

# 支持 allow_session 的工具白名单. workspace 一次性 (mount 一次写 db 就结了),
# run_python_script 脚本内容每次都变, 给"会话级免审"没意义 — 这两个 allow_session
# 退化为 allow_once. 其余 (Bash / install_libs / file_verify) 才真正走会话级缓存.
ALLOW_SESSION_TOOLS: frozenset[str] = frozenset({
    BASH_TOOL_NAME,
    INSTALL_LIBS_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
})


@dataclass
class PendingPermission:
    """一次挂起的工具审批.

    tool_input 留着是为了 router 在 allow_session 时能拿到 command (Bash) 之类的
    具体字段做"加白名单"动作 — 否则就得再问前端回传一遍, 多此一举.
    """

    tool_name: str
    tool_input: dict[str, Any]
    future: asyncio.Future[bool]


class PermissionRegistry:
    """sid → tool_use_id → PendingPermission. tool_use_id 由 SDK 保证全局唯一,
    分 sid 桶只是为了 evict session 时一键清空, 防 await 协程泄露."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, PendingPermission]] = {}

    def _bucket(self, sid: str) -> dict[str, PendingPermission]:
        return self._sessions.setdefault(sid, {})

    def register(
        self,
        sid: str,
        tool_use_id: str,
        *,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> asyncio.Future[bool]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[bool] = loop.create_future()
        self._bucket(sid)[tool_use_id] = PendingPermission(
            tool_name=tool_name, tool_input=tool_input, future=fut
        )
        return fut

    def resolve(self, sid: str, tool_use_id: str, *, allow: bool) -> PendingPermission:
        bucket = self._bucket(sid)
        pending = bucket.pop(tool_use_id, None)
        if pending is None:
            raise KeyError(tool_use_id)
        if not pending.future.done():
            pending.future.set_result(allow)
        return pending

    def peek(self, sid: str, tool_use_id: str) -> PendingPermission | None:
        return self._sessions.get(sid, {}).get(tool_use_id)

    def cleanup_session(self, sid: str) -> None:
        """session evict / delete 时调; 把所有 pending 当 deny 处理, 防协程泄露."""
        bucket = self._sessions.pop(sid, None)
        if not bucket:
            return
        for tool_use_id, p in bucket.items():
            if not p.future.done():
                p.future.set_result(False)
            logger.warning(
                f"permission auto-denied (session evicted) "
                f"sid={sid} tool_use_id={tool_use_id} tool={p.tool_name}"
            )


REGISTRY = PermissionRegistry()


@tool(
    REQUEST_TOOL_NAME,
    (
        "请求把一个本地目录挂载到当前会话工作区. 用户同意后, "
        "下一轮对话起 agent 才能读写该目录 (本轮 turn 内 fs 权限仍是旧的). "
        "参数: path (要挂载的绝对路径), reason (向用户解释为什么需要它, 中文一句话)."
    ),
    {"path": str, "reason": str},
)
async def _request_workspace_dir(args: dict[str, Any]) -> dict[str, Any]:
    """Allow 后才会被 invoke. 给 agent 一段确认信息 + 时序提示."""
    path = args.get("path", "")
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"已将 {path} 加入会话挂载目录. "
                    "提醒: 本轮 turn 用的子进程不会重启, 新目录的文件系统权限"
                    "要等下一条用户消息触发 PentaLoom 客户端重建后才生效."
                ),
            }
        ]
    }


WORKSPACE_MCP_SERVER = create_sdk_mcp_server(
    name=WORKSPACE_MCP_SERVER_NAME,
    tools=[_request_workspace_dir, *SYSTEM_RESOURCES_TOOLS],
)

# Python 环境工具自己一个 server, 跟 workspace 解耦 (职责清晰; 多一个 in-process
# server 几乎零成本). 完整工具名是 mcp__pentaloom_env__<tool>.
PYTHON_ENV_MCP_SERVER = create_sdk_mcp_server(
    name=PYTHON_ENV_MCP_SERVER_NAME,
    tools=list(PYTHON_ENV_TOOLS),
)


def _normalize_bash_command(tool_input: dict[str, Any]) -> str:
    """allow_session 命中的判定 key. 用户的"同一条命令"=完全相同的 command 字符串
    (去前后空白). description / timeout 之类参数不参与匹配, 因为 LLM 每次描述
    可能不同, timeout 同命令不同值用户感知一样."""
    return str(tool_input.get("command", "")).strip()


def _normalize_install_libs(tool_input: dict[str, Any]) -> str:
    """install_libs 的免审 key = sorted(libs) joined by '\n'. 一字不差的同组合才命中.

    不做 subset 匹配 — 那会让 LLM 拿之前授权的"numpy pandas openpyxl"反复绕,
    任何含子集的新请求都免审, 用户根本不知道又装了啥. 严匹配丑但安全.
    """
    libs = sorted(str(x).strip() for x in (tool_input.get("libs") or []) if str(x).strip())
    return "\n".join(libs)


def _normalize_file_verify(tool_input: dict[str, Any]) -> str:
    """file_verify 的免审 key = path. 同一文件本会话只问一次, 跟用户期望对齐
    (改 PPT/PDF 流程会多次跑 verify 验证, 一审一辈子审太烦)."""
    return str(tool_input.get("path", "")).strip()


def allowlist_key(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """给 (tool_name, tool_input) 算个免审 key. None 表示该工具不支持 allow_session."""
    if tool_name == BASH_TOOL_NAME:
        cmd = _normalize_bash_command(tool_input)
        return cmd or None
    if tool_name == INSTALL_LIBS_FULL_NAME:
        key = _normalize_install_libs(tool_input)
        return key or None
    if tool_name == FILE_VERIFY_FULL_NAME:
        key = _normalize_file_verify(tool_input)
        return key or None
    return None


def make_can_use_tool(sid: str, *, allowlists: dict[str, set[str]]):
    """生成闭包 sid + allowlists 的 can_use_tool callback.

    allowlists: dict[tool_name, set[免审 key]]. 引用传递 — LoomPool 持着同一个 dict,
    router 在 allow_session 时往里加 entry, 这里读到的就是最新的. dict + 每个 set
    都跟 _Entry 共生命周期, evict 时清空.
    """

    async def can_use_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        # 不在 HITL 名单的工具一律放行 (Read/Write/Edit/file_read/... 现在不审).
        if tool_name not in HITL_TOOL_NAMES:
            return PermissionResultAllow()

        # file_verify 仅当 autofix=True 才审 (会改文件); autofix=False 是 read-only.
        if tool_name == FILE_VERIFY_FULL_NAME and not bool(tool_input.get("autofix", True)):
            return PermissionResultAllow()

        tool_use_id = context.tool_use_id or ""
        if not tool_use_id:
            return PermissionResultDeny(message="missing tool_use_id from SDK")

        # 快路径: 命中本会话 allowlist 直接 allow, 不打扰用户.
        key = allowlist_key(tool_name, tool_input)
        if key is not None and key in allowlists.get(tool_name, set()):
            logger.info(
                f"{tool_name} auto-allowed (session allowlist) sid={sid} "
                f"tool_use_id={tool_use_id} key={key!r}"
            )
            return PermissionResultAllow()

        # workspace 的 path 是必填; 缺了直接 deny, 不浪费 UI 一次确认.
        if tool_name == FULL_TOOL_NAME:
            path = str(tool_input.get("path", "")).strip()
            if not path:
                return PermissionResultDeny(message="path 不能为空")

        # install_libs 必须给非空 libs.
        if tool_name == INSTALL_LIBS_FULL_NAME:
            libs = [x for x in (tool_input.get("libs") or []) if str(x).strip()]
            if not libs:
                return PermissionResultDeny(message="libs 不能为空")

        # run_python_script 必须给 script_path.
        if tool_name == RUN_SCRIPT_FULL_NAME:
            script_path = str(tool_input.get("script_path", "")).strip()
            if not script_path:
                return PermissionResultDeny(message="script_path 不能为空")

        # file_verify 必须给 path (autofix=True 才到这里, 已在前面 early return).
        if tool_name == FILE_VERIFY_FULL_NAME:
            path = str(tool_input.get("path", "")).strip()
            if not path:
                return PermissionResultDeny(message="path 不能为空")

        fut = REGISTRY.register(
            sid, tool_use_id, tool_name=tool_name, tool_input=tool_input
        )
        logger.info(
            f"permission pending sid={sid} tool_use_id={tool_use_id} tool={tool_name}"
        )
        allowed = await fut
        if allowed:
            logger.info(
                f"permission GRANTED sid={sid} tool_use_id={tool_use_id} tool={tool_name}"
            )
            return PermissionResultAllow()
        logger.info(
            f"permission DENIED sid={sid} tool_use_id={tool_use_id} tool={tool_name}"
        )
        return PermissionResultDeny(message=f"用户拒绝执行 {tool_name}")

    return can_use_tool


async def _bash_pre_tool_use_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """把 Bash 路由到 can_use_tool 的"桥".

    CLI 内建对 Bash 有"自动放行"语义, 即使我们把它从 allowed_tools 剔除,
    can_use_tool 也不会被调到. SDK 文档说:
      > can_use_tool is invoked when the CLI's permission rules evaluate to "ask"
    所以我们用 PreToolUse hook 强制返回 permissionDecision="ask", 让 CLI 把
    这次调用路由到 can_use_tool, 复用同一个 future + 前端 UI.

    Hook 本身立刻返回 (不 await 任何 future), 所以 hooks 的 60s timeout 不影响
    用户实际审批用时 — 用户慢慢决定的过程发生在 can_use_tool 的 await fut 里.
    """
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "PentaLoom HITL: Bash 需要用户审批",
        }
    }


def build_hitl_hooks():
    """给 ClaudeAgentOptions.hooks 用. 当前仅给 Bash 加 ask 路由."""
    from claude_agent_sdk import HookMatcher

    return {
        "PreToolUse": [
            HookMatcher(matcher=BASH_TOOL_NAME, hooks=[_bash_pre_tool_use_hook])
        ]
    }
