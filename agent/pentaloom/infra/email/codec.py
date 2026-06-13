"""MIME 编解码 — 构建邮件 + 解析邮件头/正文/附件.

纯 Python 实现, 不依赖外部包.
"""
from __future__ import annotations

import email
import email.header
import email.utils
import re
from email.message import EmailMessage as _StdlibEmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders as enc
from pathlib import Path
from typing import Any

from pydantic import BaseModel


# ──── 类型 ──────────────────────────────────────────────────


class EmailAddress(BaseModel):
    name: str | None = None
    email: str = ""


class EmailAttachment(BaseModel):
    filename: str = ""
    content_type: str = "application/octet-stream"
    size: int = 0


class EmailSummary(BaseModel):
    uid: str = ""
    subject: str = ""
    from_addr: EmailAddress = EmailAddress()
    date: str | None = None
    is_read: bool = False
    is_starred: bool = False
    attachment_count: int = 0


class EmailMessage(BaseModel):
    uid: str = ""
    message_id: str | None = None
    subject: str = ""
    from_addr: EmailAddress = EmailAddress()
    to_addrs: list[EmailAddress] = []
    cc_addrs: list[EmailAddress] = []
    date: str | None = None
    body_plain: str | None = None
    body_html: str | None = None
    attachments: list[EmailAttachment] = []
    is_read: bool = False
    is_starred: bool = False
    folder: str = "INBOX"


# ──── Header 解码 ──────────────────────────────────────────


def decode_header_value(raw: str | None) -> str:
    """解码 RFC 2047 编码的 header 值."""
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def parse_email_address(raw: str | None) -> EmailAddress:
    """解析 'Name <email>' 格式的地址."""
    if not raw:
        return EmailAddress()
    decoded = decode_header_value(raw)
    name, addr = email.utils.parseaddr(decoded)
    return EmailAddress(name=name or None, email=addr)


def parse_email_addresses(raw: str | None) -> list[EmailAddress]:
    """解析逗号分隔的地址列表."""
    if not raw:
        return []
    decoded = decode_header_value(raw)
    return [EmailAddress(name=n or None, email=a) for n, a in email.utils.getaddresses([decoded])]


def parse_date(raw: str | None) -> str | None:
    """解析邮件日期, 返回 ISO 格式字符串."""
    if not raw:
        return None
    decoded = decode_header_value(raw)
    try:
        parsed = email.utils.parsedate_to_datetime(decoded)
        return parsed.isoformat()
    except Exception:
        return decoded


# ──── 正文 + 附件提取 ──────────────────────────────────────


def get_body_and_attachments(
    msg: email.message.Message,
    include_body: bool = True,
    include_attachments: bool = False,
) -> tuple[str | None, str | None, list[EmailAttachment]]:
    """从 email.message.Message 提取正文和附件列表."""
    body_plain = None
    body_html = None
    attachments: list[EmailAttachment] = []

    if not msg.is_multipart():
        # 单部分邮件
        content_type = msg.get_content_type()
        disposition = str(msg.get("Content-Disposition", ""))
        payload = msg.get_payload(decode=True)
        if payload is None:
            return None, None, []
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

        if "attachment" in disposition:
            if include_attachments:
                attachments.append(EmailAttachment(
                    filename=msg.get_filename() or "attachment",
                    content_type=content_type,
                    size=len(payload),
                ))
            return None, None, attachments

        if content_type == "text/html":
            body_html = text if include_body else None
        else:
            body_plain = text if include_body else None
        return body_plain, body_html, attachments

    # 多部分邮件
    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))
        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        if "attachment" in disposition:
            if include_attachments:
                attachments.append(EmailAttachment(
                    filename=part.get_filename() or "attachment",
                    content_type=content_type,
                    size=len(payload),
                ))
            continue

        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")

        if content_type == "text/plain" and body_plain is None and include_body:
            body_plain = text
        elif content_type == "text/html" and body_html is None and include_body:
            body_html = text

    return body_plain, body_html, attachments


# ──── 构建发信 MIME ────────────────────────────────────────


class ComposeEmail(BaseModel):
    to: str
    subject: str
    body: str
    cc: str | None = None
    bcc: str | None = None
    html_body: str | None = None
    attachments: list[str] = []  # 文件绝对路径
    in_reply_to: str | None = None
    references: str | None = None


def build_mime_message(
    mail: ComposeEmail,
    from_email: str,
    display_name: str = "",
) -> EmailMessage:
    """构建发信用的 EmailMessage."""
    msg = _StdlibEmailMessage()

    # 发件人
    if display_name:
        msg["From"] = email.utils.formataddr((display_name, from_email))
    else:
        msg["From"] = from_email

    # 收件人
    msg["To"] = mail.to
    if mail.cc:
        msg["Cc"] = mail.cc

    msg["Subject"] = mail.subject

    # 回复头
    if mail.in_reply_to:
        msg["In-Reply-To"] = mail.in_reply_to
    if mail.references:
        msg["References"] = mail.references

    if mail.html_body:
        # 多部分: plain + html
        msg.set_content(mail.body)
        msg.add_alternative(mail.html_body, subtype="html")
    else:
        msg.set_content(mail.body)

    # 附件
    for fpath in mail.attachments:
        path = Path(fpath)
        if not path.is_file():
            continue
        data = path.read_bytes()
        maintype, subtype = "application", "octet-stream"
        if path.suffix in (".txt", ".md"):
            maintype, subtype = "text", "plain"
        elif path.suffix == ".pdf":
            maintype, subtype = "application", "pdf"
        elif path.suffix in (".png", ".jpg", ".jpeg", ".gif"):
            maintype = "image"
            subtype = path.suffix.lstrip(".")
            if subtype == "jpeg":
                subtype = "jpeg"
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=path.name,
        )

    return msg


# ──── 格式化摘要 ───────────────────────────────────────────


def format_email_summary(summary: EmailSummary, index: int = 0) -> str:
    """将摘要格式化为 LLM 可读的一行文本."""
    from_str = summary.from_addr.email
    if summary.from_addr.name:
        from_str = f"{summary.from_addr.name} <{summary.from_addr.email}>"
    flags = ""
    if not summary.is_read:
        flags += " [UNREAD]"
    if summary.is_starred:
        flags += " [STARRED]"
    if summary.attachment_count:
        flags += f" [{summary.attachment_count} attachment(s)]"
    prefix = f"{index}. " if index else "  "
    return f"{prefix}UID={summary.uid} | {from_str} | {summary.subject}{flags}"
