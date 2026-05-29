"""mouse: CGEvent 鼠标事件. 全部走逻辑像素 (跟 NSScreen.frame() 一致).

mouse_move 是无害操作 (移光标不点击); mouse_click 三种 kind:
  - "single"  单击 (默认)
  - "double"  双击 (两轮 down/up, kCGMouseEventClickState 1 / 2)
  - "right"   右键单击

drag 不做 (M9 范围外, v2 再加).

权限: 复用 m8 Accessibility (已授权 Python). 截图独立要 Screen Recording, 跟 mouse 无关.
"""

from __future__ import annotations

import time

from pentaloom.capabilities.computer._models import ActionResult
from pentaloom.capabilities.computer._platform import require_macos


_VALID_KINDS = frozenset({"single", "double", "right"})


def mouse_move(x: int, y: int) -> ActionResult:
    """把鼠标移到 (x, y) 逻辑像素, 不点击."""
    require_macos()
    from Quartz import (
        CGEventCreateMouseEvent,
        CGEventPost,
        kCGEventMouseMoved,
        kCGHIDEventTap,
        kCGMouseButtonLeft,
    )
    evt = CGEventCreateMouseEvent(
        None, kCGEventMouseMoved, (x, y), kCGMouseButtonLeft
    )
    CGEventPost(kCGHIDEventTap, evt)
    return ActionResult(
        action="mouse_move",
        target_description=f"({x}, {y})",
        success=True,
        message=f"鼠标已移到 ({x}, {y}) 逻辑像素",
    )


def mouse_click(x: int, y: int, kind: str = "single") -> ActionResult:
    """在 (x, y) 点击. kind: 'single' / 'double' / 'right'."""
    require_macos()
    if kind not in _VALID_KINDS:
        raise ValueError(f"kind 必须是 {sorted(_VALID_KINDS)} 之一, 收到 {kind!r}")

    from Quartz import (
        CGEventCreateMouseEvent,
        CGEventPost,
        CGEventSetIntegerValueField,
        kCGEventLeftMouseDown,
        kCGEventLeftMouseUp,
        kCGEventMouseMoved,
        kCGEventRightMouseDown,
        kCGEventRightMouseUp,
        kCGHIDEventTap,
        kCGMouseButtonLeft,
        kCGMouseButtonRight,
    )

    # 先 move 再 down/up — 有些 app 对 hover/click 时机敏感, 没 hover 直接 click
    # 会被吃掉 (浏览器 tooltip / 菜单).
    move = CGEventCreateMouseEvent(
        None, kCGEventMouseMoved, (x, y), kCGMouseButtonLeft
    )
    CGEventPost(kCGHIDEventTap, move)
    time.sleep(0.03)

    if kind == "right":
        down_type, up_type, button = (
            kCGEventRightMouseDown, kCGEventRightMouseUp, kCGMouseButtonRight,
        )
    else:
        down_type, up_type, button = (
            kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGMouseButtonLeft,
        )

    clicks = 2 if kind == "double" else 1
    for i in range(clicks):
        down = CGEventCreateMouseEvent(None, down_type, (x, y), button)
        up = CGEventCreateMouseEvent(None, up_type, (x, y), button)
        if kind == "double":
            # field 1 = kCGMouseEventClickState; 双击两轮分别填 1 / 2
            CGEventSetIntegerValueField(down, 1, i + 1)
            CGEventSetIntegerValueField(up, 1, i + 1)
        CGEventPost(kCGHIDEventTap, down)
        CGEventPost(kCGHIDEventTap, up)
        if i < clicks - 1:
            time.sleep(0.06)

    return ActionResult(
        action="mouse_click",
        target_description=f"({x}, {y}) kind={kind}",
        success=True,
        message=f"鼠标已在 ({x}, {y}) {kind} 点击",
    )
