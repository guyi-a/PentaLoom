"""browser-bridge mega-tool: 把扩展操作包成一个 `browser_bridge(action=...)` 工具.

为什么一个 mega-tool 不是 28 个独立工具:
  - 工具集中, 不污染 ToolRow UI (一行一个 chip 才看得过来)
  - 共享参数 (browser_id / page_id / index 等), 内部 action 分发
  - 跟 krow 一致, 协议层不漂

action 分支按 service.py 的方法一一对应. 失败统一走 `_unwrap_error` 把
HTTPException 转成 LLM 看得懂的 retry hint 文本 (PentaLoom 用的 SDK 没有
ModelRetry, 改成 isError=True + 描述性文本让 LLM 自然重试).
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from fastapi import HTTPException
from loguru import logger

from pentaloom.infra.browser_bridge import registry, service

# ── 命名 ─────────────────────────────────────────────────────

BROWSER_BRIDGE_MCP_SERVER_NAME = "pentaloom_browser_bridge"
BROWSER_BRIDGE_TOOL_NAME = "browser_bridge"
BROWSER_BRIDGE_FULL_NAME = (
    f"mcp__{BROWSER_BRIDGE_MCP_SERVER_NAME}__{BROWSER_BRIDGE_TOOL_NAME}"
)

# ── action 枚举 ──────────────────────────────────────────────

VALID_ACTIONS = frozenset({
    # 本地只读 — 不审批也无所谓, 但走 HITL 闸门统一
    "extension_status", "list_sessions", "list_pages", "list_windows",
    # 浏览器导航 / 标签管理
    "open_tab", "focus_page", "close_tab", "reload", "go_back",
    # 页面观察
    "read_state", "wait_for", "describe_element",
    # 交互
    "click", "hover", "dblclick", "rightclick",
    "type", "press", "scroll",
    "extract", "dropdown_options", "select_dropdown",
    "execute_script",
})

INDEX_REQUIRED = frozenset({
    "click", "hover", "dblclick", "rightclick",
    "type", "extract", "dropdown_options", "select_dropdown",
})

# 这些 action 通常依赖 read_state 给的 index, 失败时优先提示重新 read_state
INDEX_STALE_ACTIONS = frozenset({
    "click", "hover", "dblclick", "rightclick",
    "press", "type", "extract", "dropdown_options", "select_dropdown",
})


# ── helpers ──────────────────────────────────────────────────


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _ok(payload: Any) -> dict[str, Any]:
    """payload 可以是 dict / list / str. 全部 JSON 化塞进 text."""
    import json
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _pick(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    """从 service 返回里只取 Agent 关心的字段, 砍冗余 payload 降 token 消耗."""
    return {k: data[k] for k in keys if k in data}


def _unwrap_error(exc: Exception, action: str | None = None) -> dict[str, Any]:
    """把 service 抛的 HTTPException 转成 LLM 看得懂的 retry hint."""
    if not isinstance(exc, HTTPException):
        return _err(f"browser_bridge 内部错误: {exc}")

    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    lowered = detail.lower()

    # 桥不可达 / 扩展挂了
    if exc.status_code in {404, 409} and (
        "browser_id not connected" in lowered
        or "browser websocket not available" in lowered
        or "page_id not found" in lowered
    ):
        return _err(
            "Browser bridge 暂时不可用或状态过期. "
            "请先调 browser_bridge(action='extension_status') 和 "
            "browser_bridge(action='list_sessions') 重新确认; 若仍失败请提示用户检查: "
            "(1) Chrome 是否打开 (2) Kro Browser Bridge 扩展是否在 chrome://extensions/ 中启用. "
            "在这些检查没做完前不要降级到非 bridge 工具."
        )

    # 504 timeout
    if exc.status_code == 504 and action is not None:
        if action == "wait_for":
            return _err(
                "browser_bridge(wait_for) 超时. 页面可能还在加载 / 被遮挡 / settle 慢. "
                "先 read_state 看当前状态, 再决定继续等 / reload / 换思路."
            )
        if action in {"click", "press", "type"}:
            return _err(
                f"browser_bridge({action}) 超时. 页面可能繁忙 / 被弹层挡 / index 命中错元素. "
                "先 read_state 看当前状态再决定重试或换目标."
            )
        return _err(
            f"browser_bridge({action}) 超时. 页面响应慢或卡住. "
            "先 read_state 看当前状态再决定下一步."
        )

    # index 类操作失败 → 大概率 index 过期
    if action in INDEX_STALE_ACTIONS:
        return _err(
            f"Browser bridge 请求失败 (status {exc.status_code}): {detail}\n\n"
            "element index 可能已过期 (上一步操作改了 DOM). "
            "调 browser_bridge(action='read_state') 拿新 index, 用新 index 重试."
        )

    return _err(f"Browser bridge 请求失败 (status {exc.status_code}): {detail}")


# ── action 分发 ──────────────────────────────────────────────


async def _dispatch(args: dict[str, Any]) -> dict[str, Any]:
    """单一入口, 按 action 分支. 失败统一走 _unwrap_error."""
    action = str(args.get("action", "")).strip()
    if action not in VALID_ACTIONS:
        return _err(
            f"action 必须是以下之一: {', '.join(sorted(VALID_ACTIONS))}; 收到 {action!r}"
        )

    browser_id = args.get("browser_id")
    page_id = args.get("page_id")
    index = args.get("index")
    text = args.get("text")
    url = args.get("url")

    try:
        # 本地只读 — 不需要扩展也能跑
        if action == "extension_status":
            return _ok(service.extension_status())
        if action == "list_sessions":
            return _ok([s.model_dump() for s in registry.list_sessions()])
        if action == "list_pages":
            if not browser_id:
                return _err("list_pages 需要 browser_id")
            data = service.list_pages(browser_id)
            return _ok(data.get("pages", []))

        # 以下都要 browser_id (转发到扩展)
        if not browser_id:
            return _err(f"{action} 需要 browser_id (先调 extension_status / list_sessions 拿)")

        if action == "list_windows":
            data = await service.list_windows(browser_id)
            return _ok(data.get("windows", []))

        if action == "open_tab":
            if not url:
                return _err("open_tab 需要 url")
            active = bool(args.get("active", True))
            data = await service.open_tab(browser_id, url, active)
            return _ok(_pick(data, "url", "title", "active", "page_id"))

        # 以下都要 page_id
        if action != "open_tab" and not page_id:
            return _err(f"{action} 需要 page_id (先 list_pages / open_tab 拿)")

        if action == "focus_page":
            data = await service.focus_page(browser_id, page_id)
            return _ok(_pick(data, "page_id", "url", "title", "active", "focused"))

        if action == "read_state":
            data = await service.read_state(browser_id, page_id)
            md = data.get("markdown", "") if isinstance(data, dict) else ""
            return _ok(md if isinstance(md, str) else str(md))

        if action == "wait_for":
            timeout_ms = int(args.get("timeout_ms", 10000))
            data = await service.wait_for(browser_id, page_id, timeout_ms)
            return _ok(_pick(data, "waited_ms"))

        if action == "scroll":
            x = int(args.get("x", 0))
            y = int(args.get("y", 0))
            idx_int = int(index) if index is not None else None
            data = await service.scroll(browser_id, page_id, x, y, idx_int)
            return _ok(_pick(data, "scroll_x", "scroll_y"))

        if action in {"click", "hover", "dblclick", "rightclick"}:
            if index is None:
                return _err(
                    f"{action} 需要 index (从 browser_bridge(action='read_state') 拿)"
                )
            data = await service.click(browser_id, page_id, int(index), variant=action)
            return _ok(_pick(data, "index", "variant", "text", "href", "navigates"))

        if action == "type":
            if index is None or text is None:
                return _err("type 需要 index 和 text")
            data = await service.type_text(browser_id, page_id, str(text), int(index))
            return _ok(_pick(data, "index", "text"))

        if action == "press":
            key = args.get("key")
            if not key:
                return _err("press 需要 key (如 'Enter')")
            idx_int = int(index) if index is not None else None
            data = await service.press(browser_id, page_id, str(key), idx_int)
            return _ok(_pick(
                data, "key", "before_title", "before_url",
                "active_target", "submission_method", "changed",
            ))

        if action == "reload":
            await service.reload_page(browser_id, page_id)
            return _ok("reloaded")

        if action == "go_back":
            data = await service.go_back(browser_id, page_id)
            return _ok(_pick(data, "before_title", "before_url"))

        if action == "close_tab":
            data = await service.close_tab(browser_id, page_id)
            return _ok(_pick(data, "closed", "page_id"))

        if action == "extract":
            if index is None:
                return _err("extract 需要 index")
            include_html = bool(args.get("include_html", False))
            data = await service.extract(browser_id, page_id, int(index), include_html)
            return _ok(data)

        if action == "dropdown_options":
            if index is None:
                return _err("dropdown_options 需要 index")
            data = await service.dropdown_options(browser_id, page_id, int(index))
            return _ok({
                "index": data.get("index", index),
                "dropdown_type": data.get("dropdown_type", ""),
                "source": data.get("source", ""),
                "options": data.get("options", []),
            })

        if action == "select_dropdown":
            if index is None or text is None:
                return _err("select_dropdown 需要 index 和 text")
            data = await service.select_dropdown(
                browser_id, page_id, int(index), str(text)
            )
            return _ok(_pick(data, "index", "text", "value", "dropdown_type"))

        if action == "describe_element":
            if index is None:
                return _err("describe_element 需要 index")
            data = await service.describe_element(browser_id, page_id, int(index))
            return _ok(_pick(
                data, "tag", "id", "class_name", "name", "type", "role",
                "aria_label", "placeholder", "href", "text", "selector",
                "selector_matches", "attributes",
            ))

        if action == "execute_script":
            script = args.get("script")
            if not script:
                return _err("execute_script 需要 script (async function body, 必须 return)")
            data = await service.execute_script(browser_id, page_id, str(script))
            return _ok(_pick(data, "value"))

        # 理论不可达 (VALID_ACTIONS 已校验)
        return _err(f"未处理 action: {action}")

    except HTTPException as e:
        return _unwrap_error(e, action)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"browser_bridge({action}) 异常")
        return _err(f"browser_bridge({action}) 内部错误: {e}")


# ── @tool 装饰 ──────────────────────────────────────────────


@tool(
    BROWSER_BRIDGE_TOOL_NAME,
    (
        "用户真实浏览器自动化 (Chrome + Kro Browser Bridge 扩展). 用户首次调任意 action "
        "会被请求授权 (allow session 后整个会话免审). "
        "action 列表 (按使用顺序): extension_status (首次决策走 bridge 还是 use) → "
        "list_sessions → list_pages → open_tab / focus_page → read_state (拿 markdown + index) "
        "→ click N / type N text / press key / scroll / extract / select_dropdown 等. "
        "describe_element 用来把 index 升级成稳定 selector. execute_script 跑任意 JS. "
        "参数: action (枚举, 必填), 其它按 action 需要传 (browser_id / page_id / index / "
        "text / url / key / script / timeout_ms / x / y / active / include_html)."
    ),
    {
        "action": str,
        "browser_id": str,
        "page_id": str,
        "url": str,
        "index": int,
        "text": str,
        "key": str,
        "script": str,
        "timeout_ms": int,
        "x": int,
        "y": int,
        "active": bool,
        "include_html": bool,
    },
)
async def _browser_bridge_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _dispatch(args)


BROWSER_BRIDGE_MCP_SERVER = create_sdk_mcp_server(
    name=BROWSER_BRIDGE_MCP_SERVER_NAME,
    tools=[_browser_bridge_tool],
)
