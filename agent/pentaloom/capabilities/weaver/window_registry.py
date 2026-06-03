"""Window runtime registry — Phase C-2.

每个 weaver app window 启动时 ws 连后端到 /weaver/apps/<name>/window-ws.
后端把连接登记进 WindowRegistry, 当 agent 调 invoke_app(target=window) 时:

  1. 后端 invoke_app 经 registry 找这个 app 的 ws
  2. push {type: invoke, request_id, invocation_id, args} 给 window
  3. window preload 拿到 → 调 registered handler → ws send {type: invoke_result, request_id, output} 回
  4. 后端用 request_id 找到 pending Future, set_result, invoke_app 拿到 → 校 output_schema → 返 agent

不持久化, in-memory singleton. window 断开 ws 自动从 registry 移除.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import WebSocket
from loguru import logger
from starlette.websockets import WebSocketState


class WindowRegistry:
    """app_name → list[WebSocket] (一名一窗设计下基本只有 1 个).

    request_id → Future[result_or_error] 跟 invoke_app 的等待协程对齐.
    """

    _instance: "WindowRegistry | None" = None

    def __init__(self) -> None:
        self._conns: dict[str, list[WebSocket]] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @classmethod
    def instance(cls) -> "WindowRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ─── 连接管理 ───────────────────────────────────────────────

    def add(self, app_name: str, ws: WebSocket) -> None:
        self._conns.setdefault(app_name, []).append(ws)
        logger.info(f"window_registry: + {app_name} ({len(self._conns[app_name])} conn)")

    def remove(self, app_name: str, ws: WebSocket) -> None:
        lst = self._conns.get(app_name, [])
        if ws in lst:
            lst.remove(ws)
        if not lst and app_name in self._conns:
            del self._conns[app_name]
        logger.info(f"window_registry: - {app_name}")

    def get_one(self, app_name: str) -> WebSocket | None:
        """选第一个 connected ws — 顺手剔除 stale (P0.2 防腐).

        FastAPI WS 断开时 finally 段会 remove, 但 race / 异常路径可能漏掉, 留下死 ws.
        get_one 是 invoke_app 的入口, 必须保证返回的 ws 真能 send. 死 ws 直接 list 里清掉.
        """
        lst = self._conns.get(app_name)
        if not lst:
            return None
        # 倒序遍历好 list 原地 remove. WebSocketState.CONNECTED = client + server 都连着
        alive_idx: int | None = None
        for i in range(len(lst) - 1, -1, -1):
            ws = lst[i]
            if (
                ws.client_state == WebSocketState.CONNECTED
                and ws.application_state == WebSocketState.CONNECTED
            ):
                alive_idx = i  # 找到一个活的; 但继续清后面 (其实没"后面"了, 这是倒序)
            else:
                logger.warning(
                    f"window_registry: stale ws removed {app_name} "
                    f"(client={ws.client_state.name}, app={ws.application_state.name})"
                )
                lst.pop(i)
                if alive_idx is not None and i < alive_idx:
                    alive_idx -= 1
        if not lst:
            del self._conns[app_name]
            return None
        # 取第一个活的 (clean 后通常就 0 或 1 个)
        return lst[0]

    # ─── 请求-响应配对 (request_id 桥) ──────────────────────────

    def new_request(self) -> tuple[str, asyncio.Future[dict[str, Any]]]:
        request_id = uuid.uuid4().hex[:12]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = fut
        return request_id, fut

    def resolve(self, request_id: str, payload: dict[str, Any]) -> None:
        """window 发回 invoke_result / invoke_error 时调."""
        fut = self._pending.pop(request_id, None)
        if fut is None:
            logger.warning(f"window_registry: unknown request_id {request_id}")
            return
        if not fut.done():
            fut.set_result(payload)

    def drop_pending_for_ws(self, ws: WebSocket) -> None:
        """ws 断开时把它相关的 pending 全部拒, 防协程泄露.

        粗暴 — 不区分哪些 pending 是这条 ws 的; 直接全拒所有 pending.
        单 user / 单机部署下 OK; 多 window 多 app 并发时会误伤别人的 pending.
        Phase C-2 spike 范围内可接受.
        """
        for rid, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result({"type": "invoke_error", "error": "window disconnected"})
                self._pending.pop(rid, None)


def window_registry() -> WindowRegistry:
    """方便 router / runtime 拿 singleton 的 helper."""
    return WindowRegistry.instance()
