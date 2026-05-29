"""computer-use mega-tool: macOS 桌面自动化的 LLM 入口.

跟 browser_bridge 同一架构: 一个 `computer_use(action=...)` 工具, 内部按 action 分发.
理由 — 工具卡片集中, 共享参数, 跟 browser_bridge 对称, 跟 capabilities/computer 解耦.

8 个 action:
  permissions  — 检查 AX 权限, 可选 prompt 触发系统弹窗
  apps         — 列正在跑的常规 app
  snapshot     — dump 指定 app 的 AX 树 (扁平化)
  menu         — 按菜单路径执行 ["文件", "新建文件夹"]
  press        — 对 snapshot 里的 index 元素执行 AXPress
  set_value    — 给 index 元素设值 (textfield 等)
  focus        — 把指定 app 切前台
  key          — 发键盘组合 (cmd+s / escape 等)

HITL: 单 key "enabled" allowlist (跟 bridge 一致). 首次任何 action 弹审批,
allow session 后整会话所有 computer 操作免审 (用户真实电脑, 默认信任).

snapshot 返回的 elements list 是**带状态**的 — 后续 press/set_value 拿 index 反查.
但因为 SDK 工具是 stateless 的, 我们用模块级 dict 缓存最近一次 snapshot 的
raw_elements list, key 是 (pid, snapshot_id). LLM 拿 snapshot_id + index 操作.
"""

from __future__ import annotations

import uuid
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from loguru import logger

from pentaloom.capabilities.computer import service
from pentaloom.capabilities.computer._platform import is_macos

# ── 命名 ─────────────────────────────────────────────────────

COMPUTER_MCP_SERVER_NAME = "pentaloom_computer"
COMPUTER_USE_TOOL_NAME = "computer_use"
COMPUTER_USE_FULL_NAME = (
    f"mcp__{COMPUTER_MCP_SERVER_NAME}__{COMPUTER_USE_TOOL_NAME}"
)

VALID_ACTIONS = frozenset({
    "permissions", "apps", "snapshot", "menu",
    "press", "set_value", "focus", "key",
})

# ── snapshot 缓存 ────────────────────────────────────────────
# snapshot_id → (pid, raw_elements). 给 press/set_value 按 index 反查 AXUIElement.
# 同 LLM 多次 snapshot 会累积; 简单 LRU 限 16 条避免内存涨
_SNAPSHOT_CACHE: dict[str, tuple[int, list]] = {}
_SNAPSHOT_LRU: list[str] = []
_SNAPSHOT_MAX = 16


def _cache_snapshot(pid: int, raw_elements: list) -> str:
    sid = uuid.uuid4().hex[:12]
    _SNAPSHOT_CACHE[sid] = (pid, raw_elements)
    _SNAPSHOT_LRU.append(sid)
    while len(_SNAPSHOT_LRU) > _SNAPSHOT_MAX:
        old = _SNAPSHOT_LRU.pop(0)
        _SNAPSHOT_CACHE.pop(old, None)
    return sid


def _get_cached(snapshot_id: str, index: int) -> tuple[Any, str] | None:
    """返 (raw_element, description) 或 None."""
    entry = _SNAPSHOT_CACHE.get(snapshot_id)
    if entry is None:
        return None
    pid, raws = entry
    if index < 0 or index >= len(raws):
        return None
    raw = raws[index]
    # 重建 description: 用 service 模块的 attr 读, 拿 role + title 大致描述一下
    try:
        from ApplicationServices import kAXRoleAttribute, kAXTitleAttribute
        from pentaloom.capabilities.computer.service import _get_attr
        role = _get_attr(raw, kAXRoleAttribute) or "?"
        title = _get_attr(raw, kAXTitleAttribute) or ""
        desc = f"{role}[{index}] {title}".strip()
    except Exception:
        desc = f"index={index}"
    return raw, desc


# ── helpers ─────────────────────────────────────────────────


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _ok(payload: Any) -> dict[str, Any]:
    import json
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _candidates_hint(target: str) -> str:
    """target 找不到时给 LLM 一段含候选 app 名的错误提示, 省一次 list_apps 调用."""
    try:
        cands = service.list_app_candidates(target, limit=20)
    except Exception:
        return ""
    if not cands:
        return ""
    lines = [f"  - {c.name}  (bundle={c.bundle})" for c in cands]
    return "\n候选 (按相关度排序, 注意跨语言名: 比如用户说\"飞书\" 可能 app 显示为 \"Feishu\" 或 \"Lark\"):\n" + "\n".join(lines)


def _result_to_text(model: Any) -> str:
    return model.model_dump_json()


# ── action 分发 ─────────────────────────────────────────────


async def _dispatch(args: dict[str, Any]) -> dict[str, Any]:
    if not is_macos():
        return _err("computer-use 仅 macOS 支持, 当前不是 Darwin 平台.")

    action = str(args.get("action", "")).strip()
    if action not in VALID_ACTIONS:
        return _err(
            f"action 必须是: {', '.join(sorted(VALID_ACTIONS))}; 收到 {action!r}"
        )

    try:
        if action == "permissions":
            prompt = bool(args.get("prompt", False))
            r = service.check_permission(prompt=prompt)
            return _ok(_result_to_text(r))

        if action == "apps":
            apps = service.list_apps()
            return _ok([a.model_dump() for a in apps])

        if action == "snapshot":
            target = str(args.get("target", "")).strip()
            if not target:
                return _err("snapshot 需要 target (app 名或 pid)")
            depth = int(args.get("depth", service.DEFAULT_SNAPSHOT_DEPTH))
            max_children = int(args.get("max_children", service.DEFAULT_MAX_CHILDREN))
            try:
                snap, raws = service.snapshot(
                    target, depth=depth, max_children=max_children
                )
            except ValueError as e:
                return _err(str(e) + _candidates_hint(target))
            # 缓存 raw elements, 让后续 press/set_value 按 index 反查
            sid = _cache_snapshot(snap.pid, raws)
            payload = snap.model_dump()
            payload["snapshot_id"] = sid
            payload["note"] = (
                "用 snapshot_id + index 调 press/set_value. index 是瞬态, "
                "页面变了 (新窗口 / 新菜单 / scroll) 必须重新 snapshot."
            )
            return _ok(payload)

        if action == "menu":
            target = str(args.get("target", "")).strip()
            path = args.get("path")
            if not target:
                return _err("menu 需要 target (app 名)")
            if not isinstance(path, list) or not path:
                return _err(
                    "menu 需要 path (string list), 例: ['文件', '新建窗口']"
                )
            r = service.menu_action(target, [str(x) for x in path])
            # menu_action 失败 (找不到 app / 找不到 menu item) 带候选给 LLM
            if not r.success and "找不到 app" in r.message:
                return _ok(_result_to_text(r) + _candidates_hint(target))
            return _ok(_result_to_text(r))

        if action == "press":
            sid = str(args.get("snapshot_id", "")).strip()
            idx = args.get("index")
            if not sid or idx is None:
                return _err("press 需要 snapshot_id 和 index")
            entry = _get_cached(sid, int(idx))
            if entry is None:
                return _err(
                    "snapshot_id 或 index 不在缓存里 (可能过期). 重新 snapshot 拿新 id."
                )
            raw, desc = entry
            r = service.press(raw, desc)
            return _ok(_result_to_text(r))

        if action == "set_value":
            sid = str(args.get("snapshot_id", "")).strip()
            idx = args.get("index")
            value = args.get("value")
            if not sid or idx is None or value is None:
                return _err("set_value 需要 snapshot_id, index, value")
            entry = _get_cached(sid, int(idx))
            if entry is None:
                return _err(
                    "snapshot_id 或 index 不在缓存里. 重新 snapshot 拿新 id."
                )
            raw, desc = entry
            r = service.set_value(raw, str(value), desc)
            return _ok(_result_to_text(r))

        if action == "focus":
            target = str(args.get("target", "")).strip()
            if not target:
                return _err("focus 需要 target (app 名或 pid)")
            r = service.focus_app(target)
            if not r.success and "找不到 app" in r.message:
                return _ok(_result_to_text(r) + _candidates_hint(target))
            return _ok(_result_to_text(r))

        if action == "key":
            combo = str(args.get("combo", "")).strip()
            if not combo:
                return _err("key 需要 combo (例: 'cmd+s', 'escape', 'cmd+shift+t')")
            r = service.send_key(combo)
            return _ok(_result_to_text(r))

        return _err(f"未处理 action: {action}")

    except RuntimeError as e:
        # 平台问题 / 权限问题等业务错
        return _err(str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception(f"computer_use({action}) 异常")
        return _err(f"computer_use({action}) 内部错误: {e}")


# ── @tool 装饰 ──────────────────────────────────────────────


@tool(
    COMPUTER_USE_TOOL_NAME,
    (
        "macOS 桌面自动化 (Accessibility API + CGEvent). 用户首次调任意 action 弹审批, "
        "allow session 后整个会话免审. action 列表 (按典型流程): "
        "permissions (首次决策, 看是否需要让用户去开权限) → apps (列正在跑的 app) → "
        "snapshot(target=app名) (拿 AX 树 + 元素 index) → "
        "menu(target=app, path=['文件','新建']) 走菜单 / "
        "press(snapshot_id, index) 点元素 / type 类用 set_value(snapshot_id, index, value) / "
        "key(combo='cmd+s') 发快捷键 / focus(target=app) 切前台. "
        "menu 是任何 app 通用最稳; 主内容操作只在原生 app (Finder/系统设置/备忘录/Music 等) 有效, "
        "Electron app (VSCode/Chrome) 主区是黑盒, 编辑器/网页内容不要在这工具里操作 — "
        "走 file 能力 / browser_bridge."
    ),
    {
        "action": str,
        "target": str,
        "path": list[str],
        "snapshot_id": str,
        "index": int,
        "value": str,
        "combo": str,
        "depth": int,
        "max_children": int,
        "prompt": bool,
    },
)
async def _computer_use_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _dispatch(args)


COMPUTER_MCP_SERVER = create_sdk_mcp_server(
    name=COMPUTER_MCP_SERVER_NAME,
    tools=[_computer_use_tool],
)
