"""screenshot: 截屏 + 缩放 + JPEG/PNG 编码 + base64.

target 三种:
  - "screen" (默认): 整 desktop, 跨所有屏 (CGWindowListCreateImage + CGRectInfinite).
    多屏时 image 横向拼所有屏; displays 列每个屏的 logical 范围, LLM 算 mouse 该点哪屏.
  - "main_display": 只主屏 (CGDisplayCreateImage), 省 token.
  - "<app名>" / "<pid>": 截某 app 第一个 on-screen 窗口 (跨屏有效).

权限: Screen Recording (跟 Accessibility 独立 TCC).
"""

from __future__ import annotations

import base64
import io

from pentaloom.capabilities.computer._models import (
    DisplayInfo,
    PxOffset,
    PxSize,
    ScreenshotResult,
)
from pentaloom.capabilities.computer._platform import require_macos


DEFAULT_SCALE = 0.33
DEFAULT_QUALITY = 70
DEFAULT_FORMAT = "jpeg"


def _list_displays() -> tuple[list[DisplayInfo], PxSize]:
    """枚举所有屏 + 算 desktop union 逻辑尺寸.

    NSScreen 用 left-bottom 坐标; mouse / image 用 left-top. 转 left-top:
    主屏左上 = (0, 0); 副屏左上 y = main_h - (frame.origin.y + frame.size.height).
    """
    from AppKit import NSScreen

    screens = list(NSScreen.screens())
    if not screens:
        return [], PxSize(w=0, h=0)
    main = NSScreen.mainScreen()
    main_h = main.frame().size.height if main is not None else screens[0].frame().size.height

    displays: list[DisplayInfo] = []
    max_right = 0
    max_bottom = 0
    for s in screens:
        f = s.frame()
        top_left_x = int(f.origin.x)
        top_left_y = int(main_h - (f.origin.y + f.size.height))
        w = int(f.size.width)
        h = int(f.size.height)
        displays.append(DisplayInfo(
            is_main=(main is not None and s is main),
            logical_origin=PxOffset(x=top_left_x, y=top_left_y),
            logical_size=PxSize(w=w, h=h),
            scale=float(s.backingScaleFactor()),
        ))
        max_right = max(max_right, top_left_x + w)
        max_bottom = max(max_bottom, top_left_y + h)
    displays.sort(key=lambda d: (not d.is_main, d.logical_origin.x))
    return displays, PxSize(w=max_right, h=max_bottom)


def _capture_main_display():
    from Quartz import CGDisplayCreateImage, CGMainDisplayID
    return CGDisplayCreateImage(CGMainDisplayID())


def _capture_all_displays():
    """跨所有屏截图. CGRectInfinite 被 Quartz 裁到所有 on-screen window union, ≈ 整 desktop."""
    from Quartz import (
        CGRectInfinite,
        CGWindowListCreateImage,
        kCGNullWindowID,
        kCGWindowImageDefault,
        kCGWindowListOptionOnScreenOnly,
    )
    return CGWindowListCreateImage(
        CGRectInfinite,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
        kCGWindowImageDefault,
    )


def _capture_app_window(pid: int):
    """截某 app 第一个 on-screen 窗口. 跨屏有效. 找不到返 None."""
    from Quartz import (
        CGRectInfinite,
        CGWindowListCopyWindowInfo,
        CGWindowListCreateImage,
        kCGNullWindowID,
        kCGWindowImageBoundsIgnoreFraming,
        kCGWindowListOptionIncludingWindow,
        kCGWindowListOptionOnScreenOnly,
    )
    info = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
    window_id = None
    for w in info:
        if int(w.get("kCGWindowOwnerPID", -1)) != pid:
            continue
        wid = int(w.get("kCGWindowNumber", 0))
        if wid:
            window_id = wid
            break
    if window_id is None:
        return None
    return CGWindowListCreateImage(
        CGRectInfinite,
        kCGWindowListOptionIncludingWindow,
        window_id,
        kCGWindowImageBoundsIgnoreFraming,
    )


def _cgimage_to_png_bytes(image) -> bytes:
    from AppKit import NSBitmapImageRep
    rep = NSBitmapImageRep.alloc().initWithCGImage_(image)
    return bytes(rep.representationUsingType_properties_(4, None))  # type 4 = PNG


def take_screenshot(
    target: str = "screen",
    *,
    scale: float = DEFAULT_SCALE,
    quality: int = DEFAULT_QUALITY,
    format: str = DEFAULT_FORMAT,
) -> ScreenshotResult:
    require_macos()
    from PIL import Image

    if format not in ("jpeg", "png"):
        raise ValueError(f"format 必须是 'jpeg' 或 'png', 收到 {format!r}")
    if not 0.05 <= scale <= 1.0:
        raise ValueError(f"scale 必须在 [0.05, 1.0] 范围内, 收到 {scale}")
    if format == "jpeg" and not 1 <= quality <= 100:
        raise ValueError(f"quality 必须在 [1, 100] 范围内, 收到 {quality}")

    displays, desktop_logical = _list_displays()

    if target == "screen":
        cg_image = _capture_all_displays()
        if cg_image is None:
            raise RuntimeError(
                "截图失败 (CGWindowListCreateImage 返 None). 可能缺 Screen Recording 权限."
            )
        logical_for_image = desktop_logical
    elif target == "main_display":
        cg_image = _capture_main_display()
        if cg_image is None:
            raise RuntimeError(
                "截图失败 (CGDisplayCreateImage 返 None). 可能缺 Screen Recording 权限."
            )
        main = next((d for d in displays if d.is_main), None)
        logical_for_image = main.logical_size if main else desktop_logical
    else:
        from pentaloom.capabilities.computer.service import _resolve_app_pid
        pid = _resolve_app_pid(target)
        if pid is None:
            raise ValueError(f"找不到 app: {target!r}")
        cg_image = _capture_app_window(pid)
        if cg_image is None:
            raise RuntimeError(
                f"截图失败 (app {target!r} 没 on-screen 窗口, 或缺 Screen Recording 权限)."
            )
        from Quartz import CGImageGetHeight, CGImageGetWidth
        ph_w = CGImageGetWidth(cg_image)
        ph_h = CGImageGetHeight(cg_image)
        main_scale = next((d.scale for d in displays if d.is_main), 2.0)
        logical_for_image = PxSize(w=int(ph_w / main_scale), h=int(ph_h / main_scale))

    from Quartz import CGImageGetHeight, CGImageGetWidth
    physical_w = int(CGImageGetWidth(cg_image))
    physical_h = int(CGImageGetHeight(cg_image))
    scaled_w = max(1, int(physical_w * scale))
    scaled_h = max(1, int(physical_h * scale))

    png_raw = _cgimage_to_png_bytes(cg_image)
    img = Image.open(io.BytesIO(png_raw))
    if (scaled_w, scaled_h) != (physical_w, physical_h):
        img = img.resize((scaled_w, scaled_h), Image.LANCZOS)
    buf = io.BytesIO()
    if format == "jpeg":
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality)
    else:
        img.save(buf, format="PNG")
    raw = buf.getvalue()
    image_b64 = base64.b64encode(raw).decode("ascii")

    ratio = logical_for_image.w / scaled_w if scaled_w else 1.0
    if target == "screen" and len(displays) > 1:
        layout = ", ".join(
            f"{'主' if d.is_main else '副'}({d.logical_origin.x},{d.logical_origin.y})"
            f"→({d.logical_origin.x + d.logical_size.w},{d.logical_origin.y + d.logical_size.h})"
            for d in displays
        )
        note = (
            f"多屏 desktop 截图. 缩放后 ({scaled_w}×{scaled_h}); 整 desktop 逻辑 "
            f"({logical_for_image.w}×{logical_for_image.h}). 屏布局: {layout}. "
            f"换算 logical_x = image_x * {ratio:.4f}; 查 displays 看落哪个屏 "
            f"(主屏在前). mouse_click 直接传 logical 坐标."
        )
    else:
        note = (
            f"缩放后 ({scaled_w}×{scaled_h}); 逻辑 ({logical_for_image.w}×{logical_for_image.h}). "
            f"换算 logical_x = image_x * {ratio:.4f}; logical_y 同理."
        )

    return ScreenshotResult(
        image_b64=image_b64,
        format=format,
        quality=quality if format == "jpeg" else None,
        scale_applied=scale,
        target=target,
        physical_px=PxSize(w=physical_w, h=physical_h),
        logical_px=logical_for_image,
        scaled_px=PxSize(w=scaled_w, h=scaled_h),
        displays=displays,
        note=note,
    )
