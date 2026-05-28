"""跨平台浏览器探测.

browser-use CLI 可以用两种浏览器:
  1. 系统 Chrome — 任何平台用户自己装的 Google Chrome.app / chrome.exe / chrome
  2. Playwright Chromium — uvx playwright install chromium 装到本地缓存目录

任一存在就不需要装 Chromium. 探测只看文件路径, 不 spawn 子进程, 调用极廉价.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path


def is_system_chrome_installed() -> bool:
    system = platform.system()

    if system == "Darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
    elif system == "Windows":
        candidates = [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ]
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidates.append(
                Path(local_app_data)
                / "Google"
                / "Chrome"
                / "Application"
                / "chrome.exe"
            )
    else:
        candidates = [
            Path("/usr/bin/google-chrome-stable"),
            Path("/usr/bin/google-chrome"),
            Path("/usr/local/bin/google-chrome"),
        ]

    return any(p.exists() for p in candidates)


def _chromium_cache_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def is_chromium_installed() -> bool:
    """Playwright 缓存里有 chromium-* 子目录就算装了."""
    cache = _chromium_cache_dir()
    if not cache.exists():
        return False
    try:
        return any(item.name.startswith("chromium-") for item in cache.iterdir())
    except OSError:
        return False


def is_browser_available() -> bool:
    return is_system_chrome_installed() or is_chromium_installed()
