"""install_noto_sans_sc — HITL 工具, 给系统装中文字体.

跟 request_workspace_dir 共用 WORKSPACE_MCP_SERVER (server name "pentaloom"),
都属于"向用户请求一次性资源"的语义.

实现优先级:
1. macOS + brew 在 PATH: brew install --cask font-noto-sans-sc (验证过, cask 真名)
2. 兜底: 下载 GitHub release zip → 解压 .otf → 写到平台 user 字体目录:
   - macOS: ~/Library/Fonts/
   - Linux: ~/.local/share/fonts/  (装完跑 fc-cache 刷新)
   - Windows: %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts/

装完调 fonts.invalidate() + 再探一次, 装失败也清缓存 (避免污染后续会话).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
from claude_agent_sdk import tool
from loguru import logger

from pentaloom.infra import fonts

INSTALL_NOTO_SANS_SC_TOOL_NAME = "install_noto_sans_sc"
# server name 跟 workspace 同 = "pentaloom"; 完整工具名:
INSTALL_NOTO_SANS_SC_FULL_NAME = (
    f"mcp__pentaloom__{INSTALL_NOTO_SANS_SC_TOOL_NAME}"
)

# Google Noto release zip — release URL 跟 noto-cjk 仓库. 临时下载 → 解压拿 .otf.
# 用 jsdelivr 的 latest tag 太脆 (不一定有), 走 GitHub release 直链稳一点.
_NOTO_SANS_SC_ZIP_URL = (
    "https://github.com/notofonts/noto-cjk/releases/latest/download/06_NotoSansSC.zip"
)


def _user_fonts_dir() -> Path:
    """平台对应的 per-user 字体目录."""
    sys_name = platform.system()
    if sys_name == "Darwin":
        return Path.home() / "Library" / "Fonts"
    if sys_name == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("LOCALAPPDATA 未设置, 无法定位 Windows 用户字体目录")
        return Path(local) / "Microsoft" / "Windows" / "Fonts"
    # Linux / 其它 unix
    return Path.home() / ".local" / "share" / "fonts"


async def _try_brew_install() -> tuple[bool, str]:
    """macOS 优先: brew install --cask font-noto-sans-sc."""
    if platform.system() != "Darwin" or not shutil.which("brew"):
        return False, "brew not available"

    proc = await asyncio.create_subprocess_exec(
        "brew",
        "install",
        "--cask",
        "font-noto-sans-sc",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    out = (stdout or b"").decode("utf-8", "replace")
    if proc.returncode == 0:
        return True, f"brew 装好了\n--- output ---\n{out[-2048:]}"
    return False, f"brew exit={proc.returncode}\n--- output ---\n{out[-2048:]}"


async def _try_download_install() -> tuple[bool, str]:
    """兜底: 下载 zip 解压 .otf 到 user fonts dir."""
    try:
        target = _user_fonts_dir()
    except RuntimeError as e:
        return False, f"无法定位用户字体目录: {e}"

    target.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(
            timeout=120.0, follow_redirects=True
        ) as client:
            resp = await client.get(_NOTO_SANS_SC_ZIP_URL)
            resp.raise_for_status()
            data = resp.content
    except (httpx.HTTPError, OSError) as e:
        return False, f"下载失败: {e}"

    extracted: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "noto.zip"
        zip_path.write_bytes(data)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    low = name.lower()
                    if not (low.endswith(".otf") or low.endswith(".ttf")):
                        continue
                    # 只取 Regular / Variable Font, 别把整个 family 二十几个字重都扔下来
                    if "regular" not in low and "vf" not in low:
                        continue
                    out_path = target / Path(name).name
                    out_path.write_bytes(zf.read(name))
                    extracted.append(out_path.name)
        except zipfile.BadZipFile as e:
            return False, f"zip 损坏: {e}"

    if not extracted:
        return False, "zip 里没找到 .otf/.ttf 文件"

    # Linux 下要 fc-cache 才能让 fc-list 看到新字体. macOS / Windows 下放进去就生效.
    if platform.system() == "Linux" and shutil.which("fc-cache"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "fc-cache",
                "-f",
                str(target),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except OSError as e:
            logger.debug(f"[install_font] fc-cache failed (non-fatal): {e}")

    return True, f"已装到 {target}, 文件: {', '.join(extracted)}"


@tool(
    INSTALL_NOTO_SANS_SC_TOOL_NAME,
    (
        "向系统装 Noto Sans SC 字体 (中文 PPT / 中文报告必备). "
        "用户授权后才会真装. macOS 优先走 brew --cask, 失败兜底下载 GitHub "
        "release zip (~10-20MB, 首次慢). "
        "参数: reason (向用户解释为什么需要这字体, 中文一句话)."
    ),
    {"reason": str},
)
async def _install_noto_sans_sc(args: dict[str, Any]) -> dict[str, Any]:
    """can_use_tool 通过后才会被 invoke."""
    # 装之前先重探一下: 用户连续两次让装 → short-circuit, 不重复跑 brew/下载.
    fonts.invalidate()
    before = fonts.detect_cjk_fonts()
    if "Noto Sans SC" in before:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Noto Sans SC 已装好了 (无需再装). 当前系统 CJK 字体 "
                        f"(前 5): {', '.join(before[:5])}"
                    ),
                }
            ],
        }

    ok, msg = await _try_brew_install()
    if not ok:
        logger.info(
            f"[install_font] brew failed or unavailable, fallback to download: {msg}"
        )
        ok, msg = await _try_download_install()

    fonts.invalidate()
    after = fonts.detect_cjk_fonts()
    summary = (
        f"装{'成功' if ok else '失败'}: {msg}\n"
        f"当前系统 CJK 字体 (前 8): "
        f"{', '.join(after[:8]) if after else '(空)'}"
    )
    return {
        "content": [{"type": "text", "text": summary}],
        "is_error": not ok,
    }


SYSTEM_RESOURCES_TOOLS = (_install_noto_sans_sc,)
