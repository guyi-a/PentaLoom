"""browser-bridge 业务服务层.

模块状态全局: registry + 两个 dict (connections / pending_results). 进程级单例,
跨 PentaLoom session 共享 (bridge 是 user-scoped). router 在 WS 握手时写
connections, send_command 用 future 配对 result.

转发模型:
  send_command(browser_id, tool, args, command_id) →
    1. 查 registry 拿 client → 查 connections 拿 WebSocket
    2. 给 pending_results[command_id] 注册 future
    3. websocket.send_json({type:"command", id:command_id, payload:{tool, arguments}})
    4. await future ← router 收到对应 result 时 set_result, 收到 error set_exception
    5. finally 清理 future

action 方法分两类:
  本地: list_sessions / list_pages / extension_status (registry 查就行)
  转发: 其余, 都走 send_command
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import HTTPException, WebSocket
from loguru import logger

from pentaloom.infra.browser_bridge.registry import registry

# ── 模块全局: WebSocket 连接池 + 命令 future 池 ─────────────────
# router 写 connections (accept WS 时 set, disconnect 时 pop)
# send_command 读 connections + 写 pending_results
# router 读 pending_results (收到 result/error 时 set future)

connections: dict[str, WebSocket] = {}
pending_results: dict[str, asyncio.Future] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


async def send_command(
    browser_id: str,
    tool: str,
    arguments: dict[str, Any],
    command_id: str,
) -> dict[str, Any]:
    """发一条 command 到扩展, await 对应 result. 失败抛 HTTPException."""
    client = registry.get_client_by_browser(browser_id)
    if client is None:
        raise HTTPException(status_code=404, detail="browser_id not connected")

    websocket = connections.get(client.session_id)
    if websocket is None:
        # WS 已死, registry 状态过期, 清理掉再 fail.
        registry.unregister_client(client.session_id)
        raise HTTPException(status_code=409, detail="browser websocket not available")

    future: asyncio.Future = asyncio.get_running_loop().create_future()
    pending_results[command_id] = future

    try:
        await websocket.send_json({
            "type": "command",
            "id": command_id,
            "session_id": client.session_id,
            "timestamp": now_ms(),
            "payload": {
                "tool": tool,
                "arguments": arguments,
            },
        })
        return await future
    finally:
        pending_results.pop(command_id, None)


def _require_page(browser_id: str, page_id: str):
    page = registry.get_page(browser_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="page_id not found")
    return page


def _new_cmd_id() -> str:
    return uuid.uuid4().hex


# ── 本地处理 (不发 WS) ────────────────────────────────────────


def ping() -> dict[str, Any]:
    return registry.ping_payload()


def list_sessions() -> dict[str, Any]:
    return {"sessions": [s.model_dump() for s in registry.list_sessions()]}


def list_pages(browser_id: str) -> dict[str, Any]:
    return {"pages": [p.model_dump() for p in registry.list_pages(browser_id)]}


def extension_status() -> dict[str, Any]:
    """Agent 用这个一字判断走 bridge 还是 browser-use.

    ready=True 意味着至少一个扩展连上了, bridge 可用; False 则降级 browser-use.
    """
    sessions = registry.list_sessions()
    return {
        "ready": len(sessions) > 0,
        "reachable": True,  # server 自己活着这里就到, 所以恒 True
        "sessions_count": len(sessions),
        "browser_ids": [s.browser_id for s in sessions],
        "extension_versions": [
            c.extension_version
            for c in registry.clients.values()
            if c.extension_version
        ],
    }


# ── 转发到扩展 ────────────────────────────────────────────────


async def list_windows(browser_id: str) -> dict[str, Any]:
    return await send_command(
        browser_id, "browser_list_windows", {"browser_id": browser_id}, _new_cmd_id()
    )


async def focus_page(browser_id: str, page_id: str) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_focus_page",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "window_id": page.window_id,
            "tab_id": page.tab_id,
        },
        _new_cmd_id(),
    )


async def open_tab(
    browser_id: str, url: str, active: bool = True
) -> dict[str, Any]:
    return await send_command(
        browser_id,
        "browser_open_tab",
        {"browser_id": browser_id, "url": url, "active": active},
        _new_cmd_id(),
    )


async def read_state(browser_id: str, page_id: str) -> dict[str, Any]:
    """task_id 字段是 krow 给下载分桶用的, v1 砍下载所以传空 string. 扩展能不能
    容忍空 string 装好第一次跑就知道."""
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_read_state",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "task_id": "",
        },
        _new_cmd_id(),
    )


async def wait_for(
    browser_id: str, page_id: str, timeout_ms: int = 10000
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_wait_for",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "timeout_ms": timeout_ms,
        },
        _new_cmd_id(),
    )


async def scroll(
    browser_id: str, page_id: str, x: int = 0, y: int = 0, index: int | None = None
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    args: dict[str, Any] = {
        "browser_id": browser_id,
        "page_id": page_id,
        "tab_id": page.tab_id,
        "x": x,
        "y": y,
    }
    if index is not None:
        args["index"] = index
    return await send_command(browser_id, "browser_scroll", args, _new_cmd_id())


async def click(
    browser_id: str,
    page_id: str,
    index: int | None = None,
    variant: str = "click",
) -> dict[str, Any]:
    """variant: click / dblclick / rightclick / hover — 扩展端按 variant 分支."""
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_click",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "index": index,
            "variant": variant,
        },
        _new_cmd_id(),
    )


async def type_text(
    browser_id: str, page_id: str, text: str, index: int | None = None
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_type",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "index": index,
            "text": text,
        },
        _new_cmd_id(),
    )


async def press(
    browser_id: str, page_id: str, key: str, index: int | None = None
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_press",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "key": key,
            "index": index,
        },
        _new_cmd_id(),
    )


async def reload_page(
    browser_id: str, page_id: str
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_reload",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "restart_network_after_reload": False,
        },
        _new_cmd_id(),
    )


async def go_back(browser_id: str, page_id: str) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_go_back",
        {"browser_id": browser_id, "page_id": page_id, "tab_id": page.tab_id},
        _new_cmd_id(),
    )


async def close_tab(browser_id: str, page_id: str) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_close_tab",
        {"browser_id": browser_id, "page_id": page_id, "tab_id": page.tab_id},
        _new_cmd_id(),
    )


async def extract(
    browser_id: str,
    page_id: str,
    index: int | None = None,
    include_html: bool = False,
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_extract",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "index": index,
            "include_html": include_html,
        },
        _new_cmd_id(),
    )


async def dropdown_options(
    browser_id: str, page_id: str, index: int
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_dropdown_options",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "index": index,
        },
        _new_cmd_id(),
    )


async def select_dropdown(
    browser_id: str, page_id: str, index: int, text: str
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_select_dropdown",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "index": index,
            "text": text,
        },
        _new_cmd_id(),
    )


async def describe_element(
    browser_id: str, page_id: str, index: int
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_describe_element",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "index": index,
        },
        _new_cmd_id(),
    )


async def execute_script(
    browser_id: str, page_id: str, script: str
) -> dict[str, Any]:
    page = _require_page(browser_id, page_id)
    return await send_command(
        browser_id,
        "browser_execute_script",
        {
            "browser_id": browser_id,
            "page_id": page_id,
            "tab_id": page.tab_id,
            "script": script,
        },
        _new_cmd_id(),
    )
