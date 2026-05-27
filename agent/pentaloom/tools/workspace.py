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

WORKSPACE_MCP_SERVER_NAME = "pentaloom"
REQUEST_TOOL_NAME = "request_workspace_dir"
FULL_TOOL_NAME = f"mcp__{WORKSPACE_MCP_SERVER_NAME}__{REQUEST_TOOL_NAME}"

# Bash 在 app.py 的 allowed_tools 里被剔除 → 每次调用都会触发 can_use_tool.
BASH_TOOL_NAME = "Bash"

# 这两个名字以外的工具都自动放行. 列在这里方便前端用相同集合判断"哪些 tool_use 帧
# 该渲染审批按钮". 若新增需要 HITL 的工具, 改这里 + 前端 TOOLS_NEEDING_APPROVAL.
HITL_TOOL_NAMES: frozenset[str] = frozenset({FULL_TOOL_NAME, BASH_TOOL_NAME})


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
    tools=[_request_workspace_dir],
)


def _normalize_bash_command(tool_input: dict[str, Any]) -> str:
    """allow_session 命中的判定 key. 用户的"同一条命令"=完全相同的 command 字符串
    (去前后空白). description / timeout 之类参数不参与匹配, 因为 LLM 每次描述
    可能不同, timeout 同命令不同值用户感知一样."""
    return str(tool_input.get("command", "")).strip()


def make_can_use_tool(sid: str, *, bash_allowlist: set[str]):
    """生成闭包 sid + bash_allowlist 的 can_use_tool callback.

    bash_allowlist 是引用传递 — LoomPool 持着同一个 set, router 在 allow_session
    时往里加 cmd, 这里读到的就是最新的. set 跟 _Entry 共生命周期.
    """

    async def can_use_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        # 不在 HITL 名单的工具一律放行 (Read/Write/Edit/... 现在不审).
        if tool_name not in HITL_TOOL_NAMES:
            return PermissionResultAllow()

        tool_use_id = context.tool_use_id or ""
        if not tool_use_id:
            return PermissionResultDeny(message="missing tool_use_id from SDK")

        # Bash 快路径: 命中本会话白名单直接 allow, 不打扰用户.
        if tool_name == BASH_TOOL_NAME:
            cmd = _normalize_bash_command(tool_input)
            if cmd and cmd in bash_allowlist:
                logger.info(
                    f"bash auto-allowed (session allowlist) sid={sid} "
                    f"tool_use_id={tool_use_id} cmd={cmd!r}"
                )
                return PermissionResultAllow()

        # workspace 的 path 是必填; 缺了直接 deny, 不浪费 UI 一次确认.
        if tool_name == FULL_TOOL_NAME:
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
