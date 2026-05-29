"""computer 能力: macOS Accessibility / NSWorkspace / CGEvent 桌面自动化.

只 macOS. 调用方走 tools/computer_use.py 的 in-process MCP 工具. 本模块提供:
  - _platform: 平台守卫
  - _models: Pydantic 数据模型
  - service: AX/NSWorkspace/CGEvent 调用实现
"""

from pentaloom.capabilities.computer import service
from pentaloom.capabilities.computer._models import (
    ActionResult,
    AppInfo,
    AXElement,
    MenuItem,
    PermissionStatus,
    SnapshotResult,
)
from pentaloom.capabilities.computer._platform import is_macos, require_macos

__all__ = [
    "ActionResult",
    "AppInfo",
    "AXElement",
    "MenuItem",
    "PermissionStatus",
    "SnapshotResult",
    "is_macos",
    "require_macos",
    "service",
]
