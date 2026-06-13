"""SMTP / IMAP 协议客户端.

SMTPClient — 发信, smtplib.SMTP_SSL 上下文管理器.
IMAPClient — 收信, imaplib.IMAP4_SSL 上下文管理器.
"""
from __future__ import annotations

import imaplib
import smtplib
from email.message import EmailMessage
from typing import Any

from loguru import logger

from pentaloom.infra.email.presets import IMAPConfig, SMTPConfig


class SMTPClient:
    """SMTP SSL 上下文管理器, 连接 + 登录 + 发送."""

    def __init__(self, config: SMTPConfig):
        self._config = config
        self._smtp: smtplib.SMTP_SSL | smtplib.SMTP | None = None

    def __enter__(self):
        cfg = self._config
        if cfg.use_ssl:
            self._smtp = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=15)
        else:
            self._smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=15)
            self._smtp.starttls()
        self._smtp.login(cfg.username, cfg.password)
        return self

    def __exit__(self, *exc):
        if self._smtp:
            try:
                self._smtp.quit()
            except Exception:
                pass

    def send_message(self, msg: EmailMessage) -> None:
        if not self._smtp:
            raise RuntimeError("SMTPClient not connected")
        self._smtp.send_message(msg)


class IMAPError(Exception):
    pass


class IMAPClient:
    """IMAP SSL 上下文管理器, 选择文件夹 + 搜索 + 拉取."""

    def __init__(self, config: IMAPConfig):
        self._config = config
        self._imap: imaplib.IMAP4_SSL | None = None
        self._selected_folder: str | None = None

    def __enter__(self):
        cfg = self._config
        self._imap = imaplib.IMAP4_SSL(cfg.host, cfg.port)
        ok, data = self._imap.login(cfg.username, cfg.password)
        if ok != "OK":
            raise IMAPError(f"IMAP login failed: {data}")
        return self

    def __exit__(self, *exc):
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass

    # ──── 文件夹 ───────────────────────────────────────────

    def select(self, folder: str = "INBOX", readonly: bool = True) -> int:
        """选择文件夹, 返回邮件总数."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        typ, data = self._imap.select(f'"{folder}"', readonly)
        if typ != "OK":
            raise IMAPError(f"Select {folder} failed: {data}")
        self._selected_folder = folder
        return int(data[0])

    def list_folders(self) -> list[str]:
        """列出所有文件夹."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        typ, data = self._imap.list()
        folders = []
        if typ == "OK":
            for item in data:
                if isinstance(item, bytes):
                    parts = item.decode("utf-8", errors="replace").split('"')
                    if len(parts) >= 3:
                        folders.append(parts[-2])
        return folders

    # ──── 搜索 ─────────────────────────────────────────────

    def search(self, *criteria: str) -> list[bytes]:
        """IMAP SEARCH, 返回邮件序号列表."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        typ, data = self._imap.search(None, *criteria)
        if typ != "OK":
            raise IMAPError(f"Search failed: {data}")
        if not data or not data[0]:
            return []
        return data[0].split()

    # ──── 拉取 ─────────────────────────────────────────────

    def fetch(self, msg_set: str, items: str = "(UID INTERNALDATE FLAGS)") -> list[Any]:
        """IMAP FETCH."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        typ, data = self._imap.fetch(msg_set, items)
        if typ != "OK":
            raise IMAPError(f"Fetch failed: {data}")
        return data

    def uid_fetch(self, uid: str, items: str) -> list[Any]:
        """IMAP UID FETCH."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        typ, data = self._imap.uid("fetch", uid, items)
        if typ != "OK":
            raise IMAPError(f"UID Fetch failed: {data}")
        return data

    # ──── 标记/删除/移动 ──────────────────────────────────

    def uid_store(self, uid_set: str, flags_op: str, flags: str) -> Any:
        """IMAP UID STORE (标记已读/星标/删除等)."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        return self._imap.uid("store", uid_set, flags_op, flags)

    def expunge(self) -> Any:
        """IMAP EXPUNGE (永久删除已标记 \\Deleted 的邮件)."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        return self._imap.expunge()

    def uid_copy(self, uid_set: str, target_folder: str) -> Any:
        """IMAP UID COPY."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        return self._imap.uid("copy", uid_set, f'"{target_folder}"')

    def uid_move(self, uid_set: str, target_folder: str) -> Any:
        """IMAP UID MOVE (需要服务器支持 MOVE 扩展)."""
        if not self._imap:
            raise RuntimeError("IMAPClient not connected")
        return self._imap.uid("move", uid_set, f'"{target_folder}"')

    def has_capability(self, cap: str) -> bool:
        """检查 IMAP 能力."""
        if not self._imap:
            return False
        typ, data = self._imap.capability()
        if typ != "OK":
            return False
        return cap.upper() in (data[0] or b"").decode("utf-8", errors="replace").upper().split()
