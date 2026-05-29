"""平台守卫 + PyObjC 延迟导入.

computer-use 只在 macOS 工作. 这里集中守卫, 让其它模块 import 时不会因为
在 Linux/Windows 上跑而炸 — 调用 API 时才检查 platform, 抛 RuntimeError.
"""

from __future__ import annotations

import platform


def is_macos() -> bool:
    return platform.system() == "Darwin"


def require_macos() -> None:
    """供 service 层每个公共方法首行调."""
    if not is_macos():
        raise RuntimeError(
            f"computer-use 仅 macOS 支持, 当前 platform={platform.system()}"
        )
