"""cursor_overlay: macOS 鼠标 click 时屏幕涟漪 overlay.

helper 是独立 Python 子进程跑 NSApplication.run() (主线程必须). 主进程 fire-and-forget
发命令; helper 死了静默 skip, 不影响主功能.
"""

from __future__ import annotations

from pentaloom.infra.cursor_overlay.client import OverlayClient, start_helper

_active_client: OverlayClient | None = None


def set_active_client(client: OverlayClient | None) -> None:
    global _active_client
    _active_client = client


def get_active_client() -> OverlayClient | None:
    return _active_client


async def shutdown_helper(client: OverlayClient) -> None:
    await client.shutdown()


async def show_click(client: OverlayClient | None, x: int, y: int, kind: str) -> None:
    """fire-and-forget: 在 (x, y) 画 click 涟漪. client None / 死了静默 skip."""
    if client is None or not client.alive:
        return
    await client.send({"op": "ripple", "x": x, "y": y, "kind": kind})


__all__ = [
    "OverlayClient",
    "get_active_client",
    "set_active_client",
    "show_click",
    "shutdown_helper",
    "start_helper",
]
