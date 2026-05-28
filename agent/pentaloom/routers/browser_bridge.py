"""Chrome 扩展 (Kro Browser Bridge) 连接端点.

两条路由 — 路径硬编码, 因为扩展端写死, 改了扩展就连不上:
  GET/HEAD /chrome-bridge/ping  扩展定时探测, 返回 token + ws path
  WS       /chrome-bridge/ws    扩展用 token 连接

不在 /api 下 — 走的是另一套, vite proxy 也不动它. 扩展直接命中 backend 8090.

WS 帧种类 (router 手工解 dict, 不用 Pydantic 强校验):
  hello / hello_ack — 握手
  command / result / error — 配对的请求-响应 (id 关联)
  event(page_updated / page_closed / page_removed) — 扩展主动推
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from loguru import logger

from pentaloom.infra.browser_bridge import service
from pentaloom.infra.browser_bridge._protocol import (
    PING_SIG_HEADER,
    PING_SIGNATURE,
    WS_CLOSE_AUTH_FAILED,
)
from pentaloom.infra.browser_bridge.registry import registry

router = APIRouter()


@router.api_route("/chrome-bridge/ping", methods=["GET", "HEAD"])
async def chrome_bridge_ping(request: Request) -> dict[str, Any]:
    """HEAD 公开 (探活), GET 必须带 X-Kro-Client-Sig header — 跟扩展硬编码值匹配."""
    if request.method == "GET":
        sig = request.headers.get(PING_SIG_HEADER, "")
        if sig != PING_SIGNATURE:
            raise HTTPException(status_code=403, detail="Invalid client signature")
    return service.ping()


@router.websocket("/chrome-bridge/ws")
async def chrome_bridge_ws(websocket: WebSocket) -> None:
    """扩展握手 + 双向收发. token 不对直接 close, 否则进 receive loop."""
    token = websocket.query_params.get("token")
    if token != registry.discovery_token:
        await websocket.close(code=WS_CLOSE_AUTH_FAILED)
        return

    await websocket.accept()
    client = registry.register_client(browser_label="Chrome Extension")
    service.connections[client.session_id] = websocket
    logger.info(f"chrome-bridge ws connected sid={client.session_id[:8]}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("chrome-bridge: invalid JSON, ignoring")
                continue

            msg_type = message.get("type")
            payload = message.get("payload", {})

            # event 帧根据 payload.name 重新归类, 走和原生 page_updated/removed 一样的分支
            if msg_type == "event" and isinstance(payload, dict):
                name = payload.get("name")
                if name == "page_updated":
                    msg_type = "page_updated"
                elif name in {"page_closed", "page_removed"}:
                    msg_type = "page_removed"

            # ── 握手 ────────────────────────────────────────
            if msg_type == "hello":
                browser_id = payload.get("browser_id")
                browser_label = payload.get("browser_label")
                client_info = payload.get("client") or {}
                ext_version = client_info.get("extension_version")
                if isinstance(browser_id, str) and browser_id:
                    updated = registry.set_browser_id(
                        client.session_id,
                        browser_id,
                        browser_label=browser_label,
                        extension_version=ext_version,
                    )
                    if updated is not None:
                        client = updated
                await websocket.send_text(
                    json.dumps({
                        "type": "hello_ack",
                        "id": message.get("id", "hello_ack"),
                        "session_id": client.session_id,
                        "timestamp": service.now_ms(),
                        "payload": {
                            "browser_id": client.browser_id,
                            "browser_label": client.label,
                        },
                    })
                )
                logger.info(
                    f"chrome-bridge hello sid={client.session_id[:8]} "
                    f"browser_id={client.browser_id[:8]} version={ext_version}"
                )
                continue

            # ── 页面状态事件 ─────────────────────────────────
            if msg_type == "page_updated":
                bid = payload.get("browser_id")
                tab_id = payload.get("tab_id")
                window_id = payload.get("window_id")
                if (
                    isinstance(bid, str)
                    and isinstance(tab_id, int)
                    and isinstance(window_id, int)
                ):
                    ctx_role = payload.get("context_role")
                    registry.upsert_page(
                        browser_id=bid,
                        window_id=window_id,
                        tab_id=tab_id,
                        url=payload.get("url") or "",
                        title=payload.get("title") or "",
                        active=bool(payload.get("active", False)),
                        context_role=ctx_role if isinstance(ctx_role, str) else None,
                    )
                continue

            if msg_type == "page_removed":
                bid = payload.get("browser_id")
                tab_id = payload.get("tab_id")
                if isinstance(bid, str) and isinstance(tab_id, int):
                    registry.remove_page(browser_id=bid, tab_id=tab_id)
                continue

            # ── command 响应 (跟 send_command 的 future 配对) ──
            if msg_type == "result":
                command_id = message.get("id")
                future = service.pending_results.get(command_id)
                if future and not future.done():
                    future.set_result(payload)
                continue

            if msg_type == "error":
                command_id = message.get("id")
                future = service.pending_results.get(command_id)
                err = payload if isinstance(payload, dict) else {}
                detail = err.get("message") or "browser bridge command failed"
                code = err.get("code")
                if isinstance(code, str) and code:
                    detail = f"{code}: {detail}"
                logger.warning(
                    f"chrome-bridge command error id={command_id} detail={detail}"
                )
                if future and not future.done():
                    future.set_exception(HTTPException(status_code=502, detail=detail))
                continue

            logger.debug(f"chrome-bridge unhandled message: {message}")

    except WebSocketDisconnect:
        logger.info(f"chrome-bridge ws disconnected sid={client.session_id[:8]}")
    finally:
        service.connections.pop(client.session_id, None)
        # 把所有 pending future 失败掉, 防 await 协程挂在 15s timeout 上
        for cid in list(service.pending_results):
            future = service.pending_results.pop(cid, None)
            if future and not future.done():
                future.set_exception(
                    HTTPException(
                        status_code=409,
                        detail="browser websocket not available (disconnected)",
                    )
                )
        registry.unregister_client(client.session_id)
