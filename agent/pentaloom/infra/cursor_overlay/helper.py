"""cursor_overlay helper: 子进程, NSApplication.run(), 收 stdin 命令在 click 点画涟漪.

stdin 行协议 (JSON):
  {"op": "ripple", "x": int, "y": int, "kind": "single"|"double"|"right"}
  {"op": "shutdown"}
stdout 启动握手: "READY\\n".

坐标系: x/y 是 Quartz 左上原点逻辑像素 (跟 mouse_move 同空间); 内部翻转到 AppKit 左下.
"""

from __future__ import annotations

import json
import sys
import threading

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSView,
    NSWindow,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSMakeRect, NSObject, NSOperationQueue, NSTimer


RIPPLE_MAX_DIAMETER = 60.0
RIPPLE_MIN_DIAMETER = 20.0
RIPPLE_DURATION_S = 0.4
DOUBLE_RIPPLE_GAP_S = 0.05
TICK_INTERVAL_S = 1.0 / 60


class _RippleView(NSView):
    def initWithFrame_kind_(self, frame, kind):
        self = objc.super(_RippleView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._kind = kind
        self._progress = 0.0
        return self

    def setProgress_(self, p):
        self._progress = max(0.0, min(1.0, float(p)))
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):
        bounds = self.bounds()
        cx = bounds.size.width / 2
        cy = bounds.size.height / 2
        p = self._progress
        r = RIPPLE_MIN_DIAMETER / 2 + (RIPPLE_MAX_DIAMETER / 2 - RIPPLE_MIN_DIAMETER / 2) * p
        alpha = 1.0 - p
        if self._kind == "right":
            cr, cg, cb = 1.0, 0.55, 0.0
        else:
            cr, cg, cb = 1.0, 0.15, 0.15
        path = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - r, cy - r, r * 2, r * 2)
        )
        NSColor.colorWithCalibratedRed_green_blue_alpha_(cr, cg, cb, alpha * 0.5).setFill()
        path.fill()
        NSColor.colorWithCalibratedRed_green_blue_alpha_(cr, cg, cb, alpha).setStroke()
        path.setLineWidth_(2.5)
        path.stroke()


class _OverlayManager(NSObject):
    def init(self):
        self = objc.super(_OverlayManager, self).init()
        if self is None:
            return None
        self._ripples: list = []  # (window, view, elapsed_s, life_s)
        return self

    def setup(self):
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            TICK_INTERVAL_S, self, "tick:", None, True
        )

    def tick_(self, _timer):
        if not self._ripples:
            return
        still: list = []
        for win, view, elapsed, life in self._ripples:
            new_elapsed = elapsed + TICK_INTERVAL_S
            if new_elapsed >= life:
                win.orderOut_(None)
                continue
            if new_elapsed >= 0:
                view.setProgress_(new_elapsed / life)
            still.append((win, view, new_elapsed, life))
        self._ripples = still

    def spawnRipple_(self, args):
        x = float(args.get("x", 0))
        y = float(args.get("y", 0))
        kind = str(args.get("kind", "single"))
        screen = NSScreen.mainScreen()
        if screen is None:
            return
        screen_h = screen.frame().size.height
        layers = 2 if kind == "double" else 1
        for i in range(layers):
            d = RIPPLE_MAX_DIAMETER + 4
            ns_x = x - d / 2
            ns_y = (screen_h - y) - d / 2
            rect = NSMakeRect(ns_x, ns_y, d, d)
            win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                rect, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False
            )
            win.setOpaque_(False)
            win.setBackgroundColor_(NSColor.clearColor())
            win.setLevel_(NSScreenSaverWindowLevel)
            win.setIgnoresMouseEvents_(True)
            win.setCollectionBehavior_(1 | 16 | 64 | 256)
            view = _RippleView.alloc().initWithFrame_kind_(
                NSMakeRect(0, 0, d, d), kind
            )
            win.setContentView_(view)
            win.orderFrontRegardless()
            self._ripples.append((win, view, -DOUBLE_RIPPLE_GAP_S * i, RIPPLE_DURATION_S))

    def shutdownNow_(self, _sender):
        # os._exit 强退 — NSApplication.terminate_ 触发 dealloc 链对动态 RippleView 段错
        import os
        for win, _view, _elapsed, _life in self._ripples:
            win.orderOut_(None)
        self._ripples = []
        os._exit(0)


def _stdin_loop(manager: _OverlayManager) -> None:
    main_queue = NSOperationQueue.mainQueue()
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            break
        if not line:
            main_queue.addOperationWithBlock_(lambda: manager.shutdownNow_(None))
            return
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            print(f"[overlay] bad json: {line!r}", file=sys.stderr, flush=True)
            continue
        op = cmd.get("op")
        if op == "ripple":
            args = {
                "x": float(cmd.get("x", 0)),
                "y": float(cmd.get("y", 0)),
                "kind": str(cmd.get("kind", "single")),
            }
            main_queue.addOperationWithBlock_(
                lambda a=args: manager.spawnRipple_(a)
            )
        elif op == "shutdown":
            main_queue.addOperationWithBlock_(lambda: manager.shutdownNow_(None))
            return
        else:
            print(f"[overlay] unknown op: {op!r}", file=sys.stderr, flush=True)


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    manager = _OverlayManager.alloc().init()
    manager.setup()
    threading.Thread(target=_stdin_loop, args=(manager,), daemon=True).start()
    print("READY", flush=True)
    app.run()


if __name__ == "__main__":
    main()
