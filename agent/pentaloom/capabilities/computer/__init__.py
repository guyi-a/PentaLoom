"""computer 能力: macOS Accessibility / NSWorkspace / CGEvent / Quartz 桌面自动化.

只 macOS. 调用方走 tools/computer_use.py 的 in-process MCP 工具. 本模块提供:
  - _platform: 平台守卫
  - _models: Pydantic 数据模型
  - service: AX/NSWorkspace/CGEvent 调用实现
  - screenshot / mouse / paste: 视觉 + 鼠标 + 粘贴
"""

from pentaloom.capabilities.computer import mouse, paste, screenshot, service
from pentaloom.capabilities.computer._models import (
    ActionResult,
    AppInfo,
    AXElement,
    MenuItem,
    PermissionsReport,
    PxSize,
    ScreenshotResult,
    SnapshotResult,
    SubsystemPermission,
)
from pentaloom.capabilities.computer._platform import is_macos, require_macos

__all__ = [
    "ActionResult",
    "AppInfo",
    "AXElement",
    "MenuItem",
    "PermissionsReport",
    "PxSize",
    "ScreenshotResult",
    "SnapshotResult",
    "SubsystemPermission",
    "is_macos",
    "mouse",
    "paste",
    "require_macos",
    "screenshot",
    "service",
]
