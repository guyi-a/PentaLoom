"""multipart 附件流式落盘 + filename sanitize + size enforce.

为什么单独抽:
  routers/chat.py 的 /chat/with-attachments 要做 4 件事 (validate / write /
  build block / 起 turn), 写盘这块涉及流式 IO + 部分写入清理 + per-file 跟
  total 双重 size 计数, 内联会让 router 函数臃肿.

设计:
  - sanitize_filename(): 保 Unicode (含 CJK), 剥 / \\ .. NUL + 控制字符, 限长.
    剥光时 fallback 到 caller 给的 placeholder (一般是 "untitled-{i}"),
    保证返回的 string 永远非空 + 落盘安全.
  - pick_unique_dest(target_dir, filename): 返 target_dir/filename, 同名时
    自动加 (2) (3) suffix 直到不冲突. macOS / Windows file picker 同款行为.
  - commit_attachment(): UploadFile → dest 流式 read 64KB 循环写; per-file 超限
    或 total 跑超时 raise + 立即 unlink dest. caller 在外层负责清理整个
    attachments/ 目录里**这一轮新写入的文件**.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import UploadFile

# 单次 read 大小. 内存常驻 = 64KB × 并发 chunk 数, 不会随单文件大小膨胀.
CHUNK_BYTES = 64 * 1024

# filename 长度上限. macOS APFS 单文件名 255 byte (UTF-8), CJK 一字 3 byte,
# 留点 buffer 给前缀目录. 200 char 是粗略安全值.
FILENAME_MAX_CHARS = 200

# filename 里要剥的字符:
#   - / \ : 路径分隔符 (含 Windows)
#   - .. : 父目录引用 (整段替换成 _, 不是单字符删)
#   - NUL + control chars (0x00-0x1F + 0x7F)
# Unicode 字母数字 + 中日韩 + 标点不动.
_FILENAME_DROP_RE = re.compile(r"[/\\\x00-\x1f\x7f]")
_FILENAME_DOTDOT_RE = re.compile(r"\.{2,}")


class AttachmentTooLarge(Exception):
    """单文件超 per-file 上限 / 累计超 total 上限."""


def sanitize_filename(name: str, *, fallback: str) -> str:
    """剥不安全字符 + 限长. 剥光时返 fallback (caller 通常给 "untitled-{i}").

    保 Unicode 字母 / 数字 / CJK 字符 / 空格 / 大多数标点; 只剥真危险的:
    路径分隔符 / .. / NUL / 控制字符. 保留点 — 文件后缀靠它. 但前导点
    剥掉防 hidden file (.bashrc 之类).
    """
    # NFC 规范化, 防同字符不同码点撞名
    normalized = unicodedata.normalize("NFC", name)
    # 剥危险 char
    cleaned = _FILENAME_DROP_RE.sub("", normalized)
    # .. 整段替换成 _
    cleaned = _FILENAME_DOTDOT_RE.sub("_", cleaned)
    # 剥前导点 (防 .bashrc / .git 这种). 保后缀里的点 (xxx.pdf).
    cleaned = cleaned.lstrip(".")
    # 剥首尾空格 (常见用户复制 trailing space)
    cleaned = cleaned.strip()
    # 限长 — 限的是字符数不是字节数, 极端情况 (200 中文 = 600 byte) 可能超 APFS
    # 单文件名 byte 上限, 但实际不会有人取 200 字中文文件名, 不防御.
    if len(cleaned) > FILENAME_MAX_CHARS:
        # 保后缀: 找最后一个 . 后的部分, 截前面.
        if "." in cleaned[-32:]:
            stem, _, ext = cleaned.rpartition(".")
            keep = FILENAME_MAX_CHARS - len(ext) - 1
            cleaned = f"{stem[:keep]}.{ext}" if keep > 0 else cleaned[:FILENAME_MAX_CHARS]
        else:
            cleaned = cleaned[:FILENAME_MAX_CHARS]
    return cleaned or fallback


def pick_unique_dest(target_dir: Path, filename: str) -> Path:
    """返 target_dir/filename, 同名时加 (2) (3) ... suffix 直到不冲突.

    保留后缀: foo.pdf 重名 → foo (2).pdf, 不是 foo.pdf (2).
    无后缀: README 重名 → README (2).
    多附件 turn 内最多 100 次探测, 防异常情况死循环 (实际重名极少超过 5).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    candidate = target_dir / filename
    if not candidate.exists():
        return candidate
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        # 无后缀, e.g. "Dockerfile" / "README"
        stem, ext_with_dot = filename, ""
    else:
        stem, ext_with_dot = stem, f".{ext}"
    for i in range(2, 102):
        candidate = target_dir / f"{stem} ({i}){ext_with_dot}"
        if not candidate.exists():
            return candidate
    # 100 次都撞 — 极不应该发生, fallback 加 random hex 兜底
    import secrets
    candidate = target_dir / f"{stem} ({secrets.token_hex(4)}){ext_with_dot}"
    return candidate


async def commit_attachment(
    upload: "UploadFile",
    *,
    dest: Path,
    per_file_max: int,
    total_so_far: int,
    total_max: int,
) -> int:
    """流式 read upload → 写 dest, 跟踪 per-file + total size; 超限 raise + unlink.

    返实际写入字节数. caller 拿来累计 total_so_far.

    raise AttachmentTooLarge: 单文件 / 累计超限. dest 已写入的部分会被清理.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await upload.read(CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > per_file_max:
                    raise AttachmentTooLarge(
                        f"file {upload.filename!r} exceeds per-file limit "
                        f"{per_file_max} bytes"
                    )
                if total_so_far + written > total_max:
                    raise AttachmentTooLarge(
                        f"total upload exceeds {total_max} bytes "
                        f"(at file {upload.filename!r})"
                    )
                out.write(chunk)
    except AttachmentTooLarge:
        # 部分写入清理. 整 client_turn_id 目录的清理在 caller (跨文件失败时).
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return written
