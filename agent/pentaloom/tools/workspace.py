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
import json
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

from pentaloom.capabilities.browser import extract_action_verb
from pentaloom.infra.approval.destructive import is_destructive
from pentaloom.infra.approval.policy import ApprovalModeRef, get_policy
from pentaloom.infra.stream_buffer import stream_buffers
from pentaloom.tools.browser import (
    BROWSER_USE_FULL_NAME,
    INSTALL_BROWSER_USE_FULL_NAME,
    VALID_INSTALL_STEPS,
)
from pentaloom.tools.browser_bridge import (
    BROWSER_BRIDGE_FULL_NAME,
    VALID_ACTIONS as BRIDGE_VALID_ACTIONS,
)
from pentaloom.tools.computer_use import (
    COMPUTER_USE_FULL_NAME,
    VALID_ACTIONS as COMPUTER_VALID_ACTIONS,
)
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
from pentaloom.tools.search import WEB_SEARCH_FULL_NAME
from pentaloom.tools.system_resources import (
    INSTALL_NOTO_SANS_SC_FULL_NAME,
    SYSTEM_RESOURCES_TOOLS,
)
from pentaloom.tools.weaver import (
    DELETE_WEAVER_FULL_NAME,
    EDIT_WEAVER_FULL_NAME,
    INVOKE_APP_FULL_NAME,
    INVOKE_WORKFLOW_DYNAMIC_FULL_NAME,
    INVOKE_WORKFLOW_FULL_NAME,
    RUN_WEAVER_FULL_NAME,
    WEAVE_APP_FULL_NAME,
    WEAVE_SKILL_FULL_NAME,
    WEAVE_WORKFLOW_FULL_NAME,
)

WORKSPACE_MCP_SERVER_NAME = "pentaloom"
REQUEST_TOOL_NAME = "request_workspace_dir"
FULL_TOOL_NAME = f"mcp__{WORKSPACE_MCP_SERVER_NAME}__{REQUEST_TOOL_NAME}"


# Bash 在 app.py 的 allowed_tools 里被剔除 → 每次调用都会触发 can_use_tool.
BASH_TOOL_NAME = "Bash"

# SDK / CLI 内置 WebFetch. 跟 Bash 一样不带 mcp__ 前缀, 名字就是 "WebFetch".
WEB_FETCH_TOOL_NAME = "WebFetch"

# 这些名字以外的工具都自动放行. 列在这里方便前端用相同集合判断"哪些 tool_use 帧
# 该渲染审批按钮". 若新增需要 HITL 的工具, 改这里 + 前端 TOOLS_NEEDING_APPROVAL.
# file_verify 也在这里, 但 can_use_tool 内会按 input.autofix 判断: True 才审, False 直放.
# 这些名字以外的工具都自动放行. 列在这里方便前端用相同集合判断"哪些 tool_use 帧
# 该渲染审批按钮". 若新增需要 HITL 的工具, 改这里 + 前端 TOOLS_NEEDING_APPROVAL.
# file_verify 也在这里, 但 can_use_tool 内会按 input.autofix 判断: True 才审, False 直放.
# Write/Edit 在这里是 weaver/ 写防御 — 99% 调用不在 weaver/, can_use_tool 快路径 allow;
# 只有 weaver/ 内才走 deny. _weaver_write_guard_hook 把 weaver/ 内的 Write/Edit ask 路由
# 过来, 不在 weaver/ 的 Write/Edit 因为 hook 返 {} 走默认 auto-pass, 不进 can_use_tool.
HITL_TOOL_NAMES: frozenset[str] = frozenset({
    FULL_TOOL_NAME,
    BASH_TOOL_NAME,
    WEB_FETCH_TOOL_NAME,
    "Write",
    "Edit",
    INSTALL_LIBS_FULL_NAME,
    RUN_SCRIPT_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
    INSTALL_NOTO_SANS_SC_FULL_NAME,
    INSTALL_BROWSER_USE_FULL_NAME,
    BROWSER_USE_FULL_NAME,
    BROWSER_BRIDGE_FULL_NAME,
    COMPUTER_USE_FULL_NAME,
    WEB_SEARCH_FULL_NAME,
    # weaver: 沉淀 / 改 / 删 都审; list / inspect / tail_logs 免审 (只读).
    # 设计文档 §8.1-8.3: 每次单审, 不进 ALLOW_SESSION_TOOLS — 长期资产改动应该每次过目.
    WEAVE_SKILL_FULL_NAME,
    WEAVE_APP_FULL_NAME,
    WEAVE_WORKFLOW_FULL_NAME,  # M17 — 跟 weave_skill / weave_app 一档, 单审一次
    EDIT_WEAVER_FULL_NAME,
    DELETE_WEAVER_FULL_NAME,
    RUN_WEAVER_FULL_NAME,
    # invoke_app — 跑用户 weave 出来的 app. 走 ALLOW_SESSION 单 key 'enabled' 模式
    # (跟 web_search / browser_bridge / computer_use 同款): 首次任何 app 任何 invocation
    # 审批通过后整会话所有 invoke_app 调用免审. 用户自己 weave 的产物默认信任.
    INVOKE_APP_FULL_NAME,
    # invoke_workflow (M17) — 跟 invoke_app 同款 enabled-once 模式
    INVOKE_WORKFLOW_FULL_NAME,
    # invoke_workflow_dynamic (M17 dynamic) — 同款 enabled-once
    INVOKE_WORKFLOW_DYNAMIC_FULL_NAME,
})

# 支持 allow_session 的工具白名单. workspace 一次性 (mount 一次写 db 就结了),
# run_python_script 脚本内容每次都变, 给"会话级免审"没意义 — 这两个 allow_session
# 退化为 allow_once. 其余 (Bash / install_libs / file_verify / browser_* / computer_use /
# web_search) 才真正走会话级缓存.
# browser_bridge / computer_use / web_search 用单 key "enabled" — 任何 action 第一次
# 审批后整个会话所有调用免审 (用户的真实浏览器 / 真实电脑 / 搜索, 默认信任).
ALLOW_SESSION_TOOLS: frozenset[str] = frozenset({
    BASH_TOOL_NAME,
    WEB_FETCH_TOOL_NAME,
    INSTALL_LIBS_FULL_NAME,
    FILE_VERIFY_FULL_NAME,
    INSTALL_BROWSER_USE_FULL_NAME,
    BROWSER_USE_FULL_NAME,
    BROWSER_BRIDGE_FULL_NAME,
    COMPUTER_USE_FULL_NAME,
    WEB_SEARCH_FULL_NAME,
    INVOKE_APP_FULL_NAME,
    INVOKE_WORKFLOW_FULL_NAME,  # M17 — 跟 invoke_app 同款 enabled-once
    INVOKE_WORKFLOW_DYNAMIC_FULL_NAME,  # M17 dynamic — 同款 enabled-once
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


def _normalize_install_browser_use(tool_input: dict[str, Any]) -> str:
    """install_browser_use 的免审 key = step. 同一步 (check/install/chromium) 会话内
    只问一次, 重复装 / 反复 check 不再打扰用户."""
    return str(tool_input.get("step", "")).strip()


def _normalize_browser_use(tool_input: dict[str, Any]) -> str:
    """browser_use 的免审 key = action verb (open/state/click/eval/...). 同一类动作
    首次审, 后续免审. command 文本可能千变万化, 用 verb 折叠成可枚举的集合."""
    cmd = str(tool_input.get("command", "")).strip()
    if not cmd:
        return ""
    verb = extract_action_verb(cmd)
    return verb or ""


def _normalize_browser_bridge(_tool_input: dict[str, Any]) -> str:
    """browser_bridge 用单 key — 任何 action 首次审批后, 整个会话 bridge 所有调用免审.

    设计理由: bridge 操作用户真实浏览器, 用户能直接看见每一步, 默认信任. 不像
    browser_use 那样按 verb 切碎审 (那是因为 use 起的是独立机器人浏览器, 用户
    看不到, 才要按动作粒度卡)."""
    return "enabled"


def _normalize_computer_use(_tool_input: dict[str, Any]) -> str:
    """computer_use 同 browser_bridge 单 key 模式 — 用户真实电脑, 看得见每一步, 默认信任."""
    return "enabled"


def _normalize_web_search(_tool_input: dict[str, Any]) -> str:
    """web_search 同 browser_bridge 单 key 模式 — 搜索本身没破坏性, 按 query 细审
    会打断节奏; 首次审完整个会话所有 web_search 免审."""
    return "enabled"


def _normalize_invoke_app(_tool_input: dict[str, Any]) -> str:
    """invoke_app 同款单 key 模式 — 用户自己 weave 的产物默认信任,
    首次审完整会话所有 invoke_app 调用免审 (不区分 app / invocation_id 粒度).
    精细化以后做: e.g., 高 trust app permanent allow, 低 trust 每次审."""
    return "enabled"


def _normalize_invoke_workflow(_tool_input: dict[str, Any]) -> str:
    """invoke_workflow (M17) 同款单 key — 整会话首次审完所有 invoke_workflow 免审."""
    return "enabled"


def _normalize_invoke_workflow_dynamic(_tool_input: dict[str, Any]) -> str:
    """invoke_workflow_dynamic 同 invoke_workflow 同款 — 跟静态版分开统计 (避免混淆),
    但行为一致: 整会话首次审完所有 invoke_workflow_dynamic 免审."""
    return "enabled"


def _normalize_web_fetch(_tool_input: dict[str, Any]) -> str:
    """WebFetch 同款单 key 模式 — 拉单个 URL 跑小模型抽信息, 跟 web_search 一样
    没破坏性. 按 URL 切碎审会打断节奏; 首次审完整会话所有 WebFetch 免审."""
    return "enabled"


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
    if tool_name == INSTALL_BROWSER_USE_FULL_NAME:
        key = _normalize_install_browser_use(tool_input)
        return key or None
    if tool_name == BROWSER_USE_FULL_NAME:
        key = _normalize_browser_use(tool_input)
        return key or None
    if tool_name == BROWSER_BRIDGE_FULL_NAME:
        return _normalize_browser_bridge(tool_input)
    if tool_name == COMPUTER_USE_FULL_NAME:
        return _normalize_computer_use(tool_input)
    if tool_name == WEB_SEARCH_FULL_NAME:
        return _normalize_web_search(tool_input)
    if tool_name == INVOKE_APP_FULL_NAME:
        return _normalize_invoke_app(tool_input)
    if tool_name == INVOKE_WORKFLOW_FULL_NAME:
        return _normalize_invoke_workflow(tool_input)
    if tool_name == INVOKE_WORKFLOW_DYNAMIC_FULL_NAME:
        return _normalize_invoke_workflow_dynamic(tool_input)
    if tool_name == WEB_FETCH_TOOL_NAME:
        return _normalize_web_fetch(tool_input)
    return None


def make_can_use_tool(
    sid: str,
    *,
    allowlists: dict[str, set[str]],
    approval_mode_ref: ApprovalModeRef | None = None,
):
    """生成闭包 sid + allowlists + approval_mode_ref 的 can_use_tool callback.

    allowlists: dict[tool_name, set[免审 key]]. 引用传递 — LoomPool 持着同一个 dict,
    router 在 allow_session 时往里加 entry, 这里读到的就是最新的. dict + 每个 set
    都跟 _Entry 共生命周期, evict 时清空.

    approval_mode_ref: ApprovalModeRef. 三档审批策略 (default/auto/full_access).
    引用传递 — settings 改 mode 立刻生效, 不需要 rebuild client. None 时退化为
    default 行为 (现状), 兼容老调用方.
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

        # Write/Edit 走 weaver/ 写防御 — hook 把 weaver/ 内的 Write/Edit ask 路由过来,
        # 这里直接 deny. 不在 weaver/ 的 Write/Edit 因为 hook 返 {} 走默认 auto-pass,
        # 根本不会到这一层; 但保险起见这里也校 path, 万一 hook 路由错也兜得住.
        if tool_name in ("Write", "Edit"):
            from pathlib import Path as _P
            from pentaloom.capabilities.weaver.paths import weaver_root as _weaver_root
            from pentaloom.config import get_settings as _gs
            raw_path = str(tool_input.get("file_path") or "").strip()
            if raw_path:
                try:
                    abs_path = str(_P(raw_path).resolve())
                except (OSError, ValueError):
                    abs_path = ""
                if abs_path:
                    weaver_dir = str(_weaver_root(_gs()).resolve())
                    if abs_path.startswith(weaver_dir + "/") or abs_path == weaver_dir:
                        logger.warning(
                            f"can_use_tool: DENY {tool_name} {raw_path} (in weaver/)"
                        )
                        return PermissionResultDeny(
                            message=(
                                f"禁止用 {tool_name} 直接动 weaver/ 内文件. "
                                f"改 weaver 产物用 edit_weaver / weave_app_write_file / weave_app_edit_file; "
                                f"看 service log 用 tail_weaver_logs(mode='service:<name>'); "
                                f"看 manifest 用 inspect_weaver(kind='app')."
                            )
                        )
            # 不在 weaver/ 的 Write/Edit 走默认 allow (hook 实际不路由过来, 这里也兜底)
            return PermissionResultAllow()

        tool_use_id = context.tool_use_id or ""
        if not tool_use_id:
            return PermissionResultDeny(message="missing tool_use_id from SDK")

        # 三档 Policy 自动批 (default/auto/full_access).
        # destructive 永远走人工审批 — 跨所有模式 (full_access 也不能跳过).
        # 命中 destructive 不在这里 deny, 让它落到下面 register Future, 由用户拍板.
        is_destructive_call = is_destructive(tool_name, tool_input)
        if not is_destructive_call and approval_mode_ref is not None:
            policy = get_policy(approval_mode_ref.value)
            auto_approved, reason = await policy.should_auto_approve(
                tool_name, tool_input,
            )
            if auto_approved:
                logger.info(
                    f"{tool_name} auto-approved policy={approval_mode_ref.value} "
                    f"sid={sid} tool_use_id={tool_use_id} reason={reason}"
                )
                # 推 permission_resolved 帧让前端立刻 dismiss 审批栏 (跟 allowlist 命中
                # 同处理 — 否则前端只看到 tool_use, 没 dismiss 信号会闪一下审批栏).
                buf = stream_buffers.get(sid)
                if buf is not None:
                    payload = {
                        "type": "permission_resolved",
                        "tool_use_id": tool_use_id,
                        "decision": f"auto_{reason}" if reason else "auto",
                    }
                    buf.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
                return PermissionResultAllow()

        # 快路径: 命中本会话 allowlist 直接 allow, 不打扰用户.
        key = allowlist_key(tool_name, tool_input)
        if key is not None and key in allowlists.get(tool_name, set()):
            logger.info(
                f"{tool_name} auto-allowed (session allowlist) sid={sid} "
                f"tool_use_id={tool_use_id} key={key!r}"
            )
            # 推一帧 permission_resolved 让前端 reducer 立刻把 id 从 pendingApprovalIds
            # 移除, 否则前端只看到 tool_use 没看到任何 dismiss 信号, 会渲染出审批栏直到
            # tool_result 来才消失 — 视觉上"闪一下".
            buf = stream_buffers.get(sid)
            if buf is not None:
                payload = {
                    "type": "permission_resolved",
                    "tool_use_id": tool_use_id,
                    "decision": "auto_session",
                }
                buf.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
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

        # install_browser_use 必须给合法 step.
        if tool_name == INSTALL_BROWSER_USE_FULL_NAME:
            step = str(tool_input.get("step", "")).strip()
            if step not in VALID_INSTALL_STEPS:
                return PermissionResultDeny(
                    message=f"step 必须是 {'/'.join(sorted(VALID_INSTALL_STEPS))} 之一"
                )

        # browser_use 必须给非空 command.
        if tool_name == BROWSER_USE_FULL_NAME:
            cmd = str(tool_input.get("command", "")).strip()
            if not cmd:
                return PermissionResultDeny(message="command 不能为空")

        # browser_bridge 必须给合法 action.
        if tool_name == BROWSER_BRIDGE_FULL_NAME:
            act = str(tool_input.get("action", "")).strip()
            if act not in BRIDGE_VALID_ACTIONS:
                return PermissionResultDeny(
                    message=f"action 不合法; 合法值: {', '.join(sorted(BRIDGE_VALID_ACTIONS))}"
                )

        # computer_use 必须给合法 action.
        if tool_name == COMPUTER_USE_FULL_NAME:
            act = str(tool_input.get("action", "")).strip()
            if act not in COMPUTER_VALID_ACTIONS:
                return PermissionResultDeny(
                    message=f"action 不合法; 合法值: {', '.join(sorted(COMPUTER_VALID_ACTIONS))}"
                )

        # web_search 必须给非空 query.
        if tool_name == WEB_SEARCH_FULL_NAME:
            q = str(tool_input.get("query", "")).strip()
            if not q:
                return PermissionResultDeny(message="query 不能为空")

        # WebFetch 必须给非空 url + prompt.
        if tool_name == WEB_FETCH_TOOL_NAME:
            url = str(tool_input.get("url", "")).strip()
            prompt = str(tool_input.get("prompt", "")).strip()
            if not url:
                return PermissionResultDeny(message="url 不能为空")
            if not prompt:
                return PermissionResultDeny(message="prompt 不能为空")
            if not (url.startswith("http://") or url.startswith("https://")):
                return PermissionResultDeny(message="url 必须是 http/https")

        # weaver 4 工具参数校验 (设计文档 §8.1-8.3; 跨 kind 不允许重名 / 名字 kebab-case).
        if tool_name == WEAVE_SKILL_FULL_NAME:
            name = str(tool_input.get("name", "")).strip()
            desc = str(tool_input.get("description", "")).strip()
            content = str(tool_input.get("content", "")).strip()
            if not name or not desc or not content:
                return PermissionResultDeny(message="weave_skill 需要 name + description + content")
        if tool_name == EDIT_WEAVER_FULL_NAME:
            kind = str(tool_input.get("kind", "")).strip()
            name = str(tool_input.get("name", "")).strip()
            new_content = str(tool_input.get("new_content", "")).strip()
            if not kind or not name or not new_content:
                return PermissionResultDeny(message="edit_weaver 需要 kind + name + new_content")
        if tool_name == DELETE_WEAVER_FULL_NAME:
            kind = str(tool_input.get("kind", "")).strip()
            name = str(tool_input.get("name", "")).strip()
            if not kind or not name:
                return PermissionResultDeny(message="delete_weaver 需要 kind + name")
        if tool_name == RUN_WEAVER_FULL_NAME:
            kind = str(tool_input.get("kind", "")).strip()
            name = str(tool_input.get("name", "")).strip()
            if not kind or not name:
                return PermissionResultDeny(message="run_weaver 需要 kind + name")
        if tool_name == INVOKE_APP_FULL_NAME:
            app_name = str(tool_input.get("name", "")).strip()
            invocation_id = str(tool_input.get("invocation_id", "")).strip()
            if not app_name or not invocation_id:
                return PermissionResultDeny(message="invoke_app 需要 name + invocation_id")
        if tool_name == WEAVE_WORKFLOW_FULL_NAME:
            wf_name = str(tool_input.get("name", "")).strip()
            wf_desc = str(tool_input.get("description", "")).strip()
            wf_def = str(tool_input.get("definition_json", "")).strip()
            if not wf_name or not wf_desc or not wf_def:
                return PermissionResultDeny(
                    message="weave_workflow 需要 name + description + definition_json"
                )
        if tool_name == INVOKE_WORKFLOW_FULL_NAME:
            wf_name = str(tool_input.get("name", "")).strip()
            if not wf_name:
                return PermissionResultDeny(message="invoke_workflow 需要 name")
        if tool_name == INVOKE_WORKFLOW_DYNAMIC_FULL_NAME:
            wf_name = str(tool_input.get("name", "")).strip()
            if not wf_name:
                return PermissionResultDeny(message="invoke_workflow_dynamic 需要 name")

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


async def _weaver_write_guard_hook(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """硬护栏: 禁止 Write/Edit 落在 data_dir/weaver/ 之下.

    历史: 最初设计想拦 Read/Write/Edit/Glob/Grep 所有内置 fs 工具, 但 SDK 0.2.87
    的 PreToolUse hook 对 Read/Glob/Grep 这些"内置 auto-pass"工具不触发 (实测 deny
    返回值被 CLI 忽略, hook 函数本身根本没被调到). **只有 Write/Edit 可以**走
    "ask" 路由到 can_use_tool 真拦得住 (Bash 同理).

    所以这层只防"写", 不防"读". 读 weaver/ 走 prompt 铁律 + 给 agent 提供合理路径:
    看 service log 用 tail_weaver_logs(mode='service:<name>'), 看 manifest 用
    inspect_weaver(kind='app'), 用户硬要 Read 也只是看不到破坏.

    策略: 路径在 weaver/ → 返 ask 路由到 can_use_tool, can_use_tool 检查路径 deny;
    不在 weaver/ → 返 allow 走默认 auto-pass (不进 can_use_tool 不耗 roundtrip).
    """
    from pathlib import Path as _P

    from pentaloom.capabilities.weaver.paths import weaver_root
    from pentaloom.config import get_settings

    raw = input_data.get("file_path") or input_data.get("path") or ""
    if not raw:
        # 没 path 字段不管, 让默认放行
        return {}

    try:
        abs_path = str(_P(str(raw)).resolve())
    except (OSError, ValueError):
        return {}

    settings = get_settings()
    weaver_dir = str(weaver_root(settings).resolve())

    if not abs_path.startswith(weaver_dir + "/") and abs_path != weaver_dir:
        # 不在 weaver/ 之下 — 显式 allow 走默认放行 (其实不返也行, default 就是 allow)
        return {}

    # 在 weaver/ 之下 — 路由到 can_use_tool, 那里实施 deny.
    # 不能在 hook 直接 deny 因为 SDK 对 hook deny 的处理可能让 agent 收不到清晰错误.
    logger.warning(
        f"weaver_write_guard: route to ask path={abs_path}"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "PentaLoom: write to weaver/ goes through can_use_tool deny",
        }
    }


def build_hitl_hooks():
    """给 ClaudeAgentOptions.hooks 用.

    PreToolUse hook 注册:
      1. Bash → 永远 ask 路由 (HITL 入口, can_use_tool 弹审)
      2. Write/Edit → path 在 weaver/ 才 ask 路由 (can_use_tool 真 deny);
         其他 path 不管 (auto-pass). Read/Glob/Grep 不挂 hook (SDK 限制, 见 _weaver_write_guard_hook).
    """
    from claude_agent_sdk import HookMatcher

    return {
        "PreToolUse": [
            HookMatcher(matcher=BASH_TOOL_NAME, hooks=[_bash_pre_tool_use_hook]),
            HookMatcher(matcher="Write", hooks=[_weaver_write_guard_hook]),
            HookMatcher(matcher="Edit", hooks=[_weaver_write_guard_hook]),
        ]
    }
