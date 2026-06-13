"""email mega-tool: 收/发/搜索/标记/删除邮件.

一个 mega-tool 而不是 12 个独立工具:
  - 工具集中, 不污染 ToolRow UI
  - 共享参数 (account_id / folder / uid 等), 内部 action 分发
  - 跟 browser_bridge 一致的结构模式

action 列表:
  读取: fetch_emails / search_emails / get_email_content / list_folders
  发送: send_email / reply_email / forward_email
  管理: delete_email / mark_email / move_email
  配置: check_email_config

未配置时返回友好提示, 引导用户去设置页配置.
"""
from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import re
import smtplib
import time as time_module
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from loguru import logger

from pentaloom.config import get_settings
from pentaloom.infra.email import (
    PROVIDER_PRESETS,
    EmailAccount,
    IMAPClient,
    IMAPError,
    SMTPClient,
    account_to_imap_config,
    account_to_smtp_config,
    get_account_by_id,
    get_default_account,
)
from pentaloom.infra.email.codec import (
    ComposeEmail,
    EmailAddress,
    EmailAttachment,
    EmailMessage,
    EmailSummary,
    build_mime_message,
    decode_header_value,
    format_email_summary,
    get_body_and_attachments,
    parse_date,
    parse_email_address,
    parse_email_addresses,
)

# ── 命名 ─────────────────────────────────────────────────────

EMAIL_MCP_SERVER_NAME = "pentaloom_email"
EMAIL_TOOL_NAME = "email"
EMAIL_FULL_NAME = f"mcp__{EMAIL_MCP_SERVER_NAME}__{EMAIL_TOOL_NAME}"

# ── action 枚举 ──────────────────────────────────────────────

VALID_ACTIONS = frozenset({
    # 读取
    "fetch_emails", "search_emails", "get_email_content", "list_folders",
    # 发送
    "send_email", "reply_email", "forward_email",
    # 管理
    "delete_email", "mark_email", "move_email",
    # 配置
    "check_email_config",
})

# ── 文件夹映射 ──────────────────────────────────────────────

FOLDER_ALIAS: dict[str, dict[str, str | None]] = {
    "gmail": {
        "inbox": "INBOX",
        "sent": "[Gmail]/Sent Mail",
        "drafts": "[Gmail]/Drafts",
        "trash": "[Gmail]/Trash",
        "spam": "[Gmail]/Spam",
        "starred": None,  # 用 FLAGGED 搜索
    },
    "qq": {
        "inbox": "INBOX",
        "sent": "Sent Messages",
        "drafts": "Drafts",
        "trash": "Deleted Messages",
        "spam": "Junk",
        "starred": None,
    },
}

_NOT_CONFIGURED_HINT = (
    "邮箱未配置。请引导用户到设置页 > 连接 > 邮箱 > 添加邮箱账号。\n"
    "支持 Gmail 和 QQ 邮箱。QQ 邮箱需要使用授权码（不是 QQ 密码）。"
)


# ── helpers ──────────────────────────────────────────────────


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _ok(payload: Any) -> dict[str, Any]:
    import json
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


def _data_dir():
    return get_settings().data_dir


def _get_account(account_id: str | None = None) -> tuple[EmailAccount | None, str | None]:
    """获取账号, 返回 (account, error_hint)."""
    if account_id:
        acc = get_account_by_id(_data_dir(), account_id)
    else:
        acc = get_default_account(_data_dir())
    if not acc:
        return None, _NOT_CONFIGURED_HINT
    return acc, None


def _resolve_folder(provider: str, folder: str) -> tuple[str, bool]:
    """解析文件夹别名, 返回 (实际文件夹名, 是否按星标搜索)."""
    fl = folder.lower()
    mapping = FOLDER_ALIAS.get(provider, {})
    if fl in mapping:
        resolved = mapping[fl]
        if fl == "starred" and resolved is None:
            return "INBOX", True
        return resolved or folder, False
    return folder, False


# ── SearchCriteria ──────────────────────────────────────────


@dataclass
class SearchCriteria:
    unread_only: bool = False
    starred_only: bool = False
    search_from: str | None = None
    search_subject: str | None = None
    search_since: str | None = None
    search_before: str | None = None

    def to_imap_criteria(self) -> list[str]:
        parts = []
        if self.unread_only:
            parts.append("UNSEEN")
        if self.starred_only:
            parts.append("FLAGGED")
        if self.search_from:
            parts.extend(["FROM", f'"{self.search_from}"'])
        if self.search_subject:
            parts.extend(["SUBJECT", f'"{self.search_subject}"'])
        if self.search_since:
            parts.append(f"SINCE {self.search_since}")
        if self.search_before:
            parts.append(f"BEFORE {self.search_before}")
        return parts or ["ALL"]


# ── IMAP 操作 (asyncio.to_thread 包装) ────────────────────


def _fetch_emails_imap(config, provider: str, folder: str = "INBOX",
                       limit: int = 20, offset: int = 0,
                       criteria: SearchCriteria | None = None) -> dict:
    """IMAP 拉取邮件摘要."""
    criteria = criteria or SearchCriteria()
    try:
        with IMAPClient(config) as client:
            folder_total = client.select(folder, readonly=True)
            imap_crit = criteria.to_imap_criteria()
            msg_nums = client.search(*imap_crit)
            if not msg_nums:
                return {"ok": True, "emails": [], "total_count": 0}

            # 拉元数据并排序
            msg_set = b",".join(msg_nums).decode("ascii")
            raw_data = client.fetch(msg_set, "(UID INTERNALDATE FLAGS)")
            msg_list = _parse_fetch_response(raw_data)
            msg_list.sort(key=lambda x: x[2], reverse=True)
            total = len(msg_list)
            page = msg_list[offset:offset + limit]

            if not page:
                return {"ok": True, "emails": [], "total_count": total}

            # 拉摘要
            page_set = b",".join(m[0] for m in page).decode("ascii")
            data = client.fetch(page_set, "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
            emails = _parse_summaries(data, page)
            return {"ok": True, "emails": emails, "total_count": total}
    except (IMAPError, imaplib.IMAP4.error) as e:
        err = str(e).lower()
        if "authentication" in err:
            return {"ok": False, "error": f"认证失败: {e}", "error_code": "AUTH_FAILED"}
        return {"ok": False, "error": f"IMAP 错误: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"错误: {e}"}


def _get_email_imap(config, uid: str, folder: str = "INBOX",
                    mark_as_read: bool = False) -> dict:
    """IMAP 按 UID 读全文."""
    try:
        with IMAPClient(config) as client:
            client.select(folder, readonly=not mark_as_read)
            fetch_cmd = "BODY[]" if mark_as_read else "BODY.PEEK[]"
            data = client.uid_fetch(uid, f"(FLAGS {fetch_cmd})")
            if not data or not data[0] or not isinstance(data[0], tuple):
                return {"ok": False, "error": f"邮件未找到: {uid}"}
            response_line, email_data = data[0][0], data[0][1]
            msg = email_lib.message_from_bytes(email_data)
            body_plain, body_html, attachments = get_body_and_attachments(msg, include_body=True)
            return {
                "ok": True,
                "email": {
                    "uid": uid,
                    "message_id": msg.get("Message-ID"),
                    "subject": decode_header_value(msg.get("Subject")),
                    "from": parse_email_address(msg.get("From")).model_dump(),
                    "to": [a.model_dump() for a in parse_email_addresses(msg.get("To"))],
                    "cc": [a.model_dump() for a in parse_email_addresses(msg.get("Cc"))],
                    "date": parse_date(msg.get("Date")),
                    "body_plain": body_plain,
                    "body_html": body_html[:5000] if body_html else None,
                    "attachments": [a.model_dump() for a in attachments],
                    "is_read": b"\\Seen" in response_line,
                    "is_starred": b"\\Flagged" in response_line,
                    "folder": folder,
                },
            }
    except (IMAPError, imaplib.IMAP4.error) as e:
        return {"ok": False, "error": f"IMAP 错误: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"错误: {e}"}


def _parse_fetch_response(fetch_data: list) -> list[tuple[bytes, str, float]]:
    """解析 FETCH 响应, 返回 [(msg_num, uid, timestamp), ...]."""
    results = []
    for item in fetch_data:
        if isinstance(item, tuple) and len(item) >= 1:
            response = item[0] if isinstance(item[0], bytes) else b""
        elif isinstance(item, bytes):
            response = item
        else:
            continue
        seq_match = re.match(rb"(\d+)\s+\(", response)
        if not seq_match:
            continue
        uid_match = re.search(rb"UID\s+(\d+)", response)
        uid_str = uid_match.group(1).decode("ascii") if uid_match else seq_match.group(1).decode("ascii")
        date_tuple = imaplib.Internaldate2tuple(response)
        timestamp = time_module.mktime(date_tuple) if date_tuple else 0
        results.append((seq_match.group(1), uid_str, timestamp))
    return results


def _parse_summaries(fetch_data: list, page: list) -> list[dict]:
    """解析邮件摘要."""
    summaries = {}
    for item in fetch_data:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        response = item[0] if isinstance(item[0], bytes) else b""
        header_data = item[1] if isinstance(item[1], bytes) else b""
        uid_match = re.search(rb"UID\s+(\d+)", response)
        if not uid_match:
            continue
        uid_str = uid_match.group(1).decode("ascii")
        msg = email_lib.message_from_bytes(header_data) if header_data else None
        summaries[uid_str] = {
            "uid": uid_str,
            "subject": decode_header_value(msg.get("Subject")) if msg else "",
            "from": parse_email_address(msg.get("From")).model_dump() if msg else {},
            "date": parse_date(msg.get("Date")) if msg else None,
            "is_read": b"\\Seen" in response,
            "is_starred": b"\\Flagged" in response,
        }
    return [summaries.get(uid, {}) for _, uid, _ in page if uid in summaries]


# ── dispatch ──────────────────────────────────────────────


async def _dispatch(args: dict[str, Any]) -> dict[str, Any]:
    action = args.get("action", "")
    if action not in VALID_ACTIONS:
        return _err(f"Unknown action: {action}. Valid: {', '.join(sorted(VALID_ACTIONS))}")

    try:
        handler = _ACTION_MAP[action]
        return await handler(args)
    except Exception as e:
        logger.error(f"email tool error: {e}")
        return _err(f"Email tool error ({action}): {e}")


async def _fetch_emails(args: dict) -> dict:
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    folder_alias = args.get("folder", "inbox")
    resolved, is_starred = _resolve_folder(acc.provider, folder_alias)
    limit = min(max(1, args.get("limit", 10)), 50)
    unread_only = args.get("unread_only", False)
    criteria = SearchCriteria(unread_only=unread_only, starred_only=is_starred)
    result = await asyncio.to_thread(_fetch_emails_imap, config, acc.provider, resolved, limit, criteria=criteria)
    if not result.get("ok"):
        return _ok(f"拉取邮件失败: {result.get('error', '未知错误')}")
    if not result.get("emails"):
        return _ok(f"{'未读' if unread_only else ''}邮件为空 ({folder_alias})")
    lines = [f"找到 {len(result['emails'])} 封邮件 (共 {result.get('total_count', 0)}):", ""]
    for i, em in enumerate(result["emails"], 1):
        subj = em.get("subject", "(无主题)")
        from_addr = em.get("from", {})
        from_str = f"{from_addr.get('name', '')} <{from_addr.get('email', '')}>" if from_addr.get("email") else "未知"
        flags = ""
        if not em.get("is_read", True):
            flags += " [未读]"
        if em.get("is_starred"):
            flags += " [星标]"
        lines.append(f"{i}. UID={em.get('uid','?')} | {from_str} | {subj}{flags}")
    lines.append("\n使用 get_email_content(uid='...') 查看完整内容")
    return _ok("\n".join(lines))


async def _search_emails(args: dict) -> dict:
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    folder_alias = args.get("folder", "inbox")
    resolved, is_starred = _resolve_folder(acc.provider, folder_alias)
    if is_starred:
        args["starred_only"] = True
    criteria = SearchCriteria(
        unread_only=args.get("unread_only", False),
        starred_only=args.get("starred_only", False),
        search_from=args.get("from_addr"),
        search_subject=args.get("subject"),
        search_since=args.get("since"),
        search_before=args.get("before"),
    )
    if not any([criteria.unread_only, criteria.starred_only, criteria.search_from,
                criteria.search_subject, criteria.search_since, criteria.search_before]):
        return _ok("请至少提供一个搜索条件 (from_addr / subject / since / before / unread_only / starred_only)")
    limit = min(max(1, args.get("limit", 20)), 50)
    result = await asyncio.to_thread(_fetch_emails_imap, config, acc.provider, resolved, limit, criteria=criteria)
    if not result.get("ok"):
        return _ok(f"搜索失败: {result.get('error', '未知错误')}")
    if not result.get("emails"):
        return _ok("没有匹配的邮件")
    lines = [f"搜索到 {len(result['emails'])} 封邮件:", ""]
    for i, em in enumerate(result["emails"], 1):
        lines.append(f"{i}. UID={em.get('uid','?')} | {em.get('subject', '')}")
    lines.append("\n使用 get_email_content(uid='...') 查看完整内容")
    return _ok("\n".join(lines))


async def _get_email_content(args: dict) -> dict:
    uid = args.get("uid", "")
    if not uid:
        return _err("缺少 uid 参数")
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    folder_alias = args.get("folder", "inbox")
    resolved, _ = _resolve_folder(acc.provider, folder_alias)
    mark_read = args.get("mark_as_read", False)
    result = await asyncio.to_thread(_get_email_imap, config, uid, resolved, mark_as_read)
    if not result.get("ok"):
        return _ok(f"获取邮件失败: {result.get('error', '未知错误')}")
    em = result["email"]
    lines = ["=" * 50]
    from_addr = em.get("from", {})
    from_str = f"{from_addr.get('name', '')} <{from_addr.get('email', '')}>" if from_addr.get("email") else "未知"
    lines.append(f"From: {from_str}")
    if em.get("to"):
        to_strs = [f"{a.get('name','')} <{a.get('email','')}>" if a.get("name") else a.get("email","") for a in em["to"]]
        lines.append(f"To: {', '.join(to_strs)}")
    if em.get("date"):
        lines.append(f"Date: {em['date']}")
    lines.append(f"Subject: {em.get('subject', '(无主题)')}")
    lines.append(f"Status: {'已读' if em.get('is_read') else '未读'}{', 星标' if em.get('is_starred') else ''}")
    if em.get("attachments"):
        lines.append(f"附件 ({len(em['attachments'])}个):")
        for att in em["attachments"]:
            lines.append(f"  - {att.get('filename', '?')} [{att.get('content_type', '?')}]")
    lines.append("=" * 50)
    lines.append("")
    if em.get("body_plain"):
        lines.append(em["body_plain"].strip())
    elif em.get("body_html"):
        text = re.sub(r'<[^>]+>', '', em["body_html"])
        text = re.sub(r'\s+', ' ', text).strip()
        lines.append(text[:2000] + ("..." if len(text) > 2000 else ""))
    else:
        lines.append("(无正文)")
    return _ok("\n".join(lines))


async def _list_folders(args: dict) -> dict:
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    try:
        folders = await asyncio.to_thread(lambda: IMAPClient(config).__enter__().list_folders() if False else _list_folders_sync(config))
        return _ok(f"可用文件夹: {', '.join(folders)}\n\n别名: inbox, sent, drafts, trash, spam, starred")
    except Exception as e:
        return _ok(f"获取文件夹失败: {e}")


def _list_folders_sync(config) -> list[str]:
    with IMAPClient(config) as client:
        return client.list_folders()


async def _send_email(args: dict) -> dict:
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    to = args.get("to", "")
    subject = args.get("subject", "")
    body = args.get("body", "")
    if not to or not subject:
        return _err("缺少 to 或 subject 参数")
    mail = ComposeEmail(
        to=to, subject=subject, body=body,
        cc=args.get("cc"),
        html_body=args.get("html_body"),
        attachments=args.get("attachments", []),
    )
    msg = build_mime_message(mail, from_email=acc.email, display_name=acc.display_name)
    try:
        config = account_to_smtp_config(acc)
        def _send():
            with SMTPClient(config) as client:
                client.send_message(msg)
        await asyncio.to_thread(_send)
        return _ok(f"邮件发送成功!\nTo: {to}\nSubject: {subject}")
    except smtplib.SMTPAuthenticationError:
        return _err("认证失败，请检查邮箱配置")
    except Exception as e:
        return _err(f"发送失败: {e}")


async def _reply_email(args: dict) -> dict:
    uid = args.get("uid", "")
    body = args.get("body", "")
    if not uid or not body:
        return _err("缺少 uid 或 body 参数")
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    folder_alias = args.get("folder", "inbox")
    resolved, _ = _resolve_folder(acc.provider, folder_alias)
    # 获取原邮件
    get_result = await asyncio.to_thread(_get_email_imap, config, uid, resolved, mark_as_read=False)
    if not get_result.get("ok"):
        return _ok(f"获取原邮件失败: {get_result.get('error')}")
    original = get_result["email"]
    # 构建回复
    reply_to = original.get("from", {}).get("email", "")
    subject = original.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    from_str = original.get("from", {}).get("email", "")
    if original.get("from", {}).get("name"):
        from_str = f"{original['from']['name']} <{original['from']['email']}>"
    original_body = original.get("body_plain", "")
    quoted = f"\n\nOn {original.get('date', 'unknown date')}, {from_str} wrote:\n"
    for line in (original_body or "").split("\n"):
        quoted += f"> {line}\n"
    full_body = body + quoted
    mail = ComposeEmail(
        to=reply_to, subject=subject, body=full_body,
        in_reply_to=original.get("message_id"),
        references=original.get("message_id"),
    )
    msg = build_mime_message(mail, from_email=acc.email, display_name=acc.display_name)
    try:
        smtp_config = account_to_smtp_config(acc)
        def _send():
            with SMTPClient(smtp_config) as client:
                client.send_message(msg)
        await asyncio.to_thread(_send)
        return _ok(f"回复发送成功!\nTo: {reply_to}\nSubject: {subject}")
    except Exception as e:
        return _err(f"回复发送失败: {e}")


async def _forward_email(args: dict) -> dict:
    uid = args.get("uid", "")
    to = args.get("to", "")
    if not uid or not to:
        return _err("缺少 uid 或 to 参数")
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    folder_alias = args.get("folder", "inbox")
    resolved, _ = _resolve_folder(acc.provider, folder_alias)
    get_result = await asyncio.to_thread(_get_email_imap, config, uid, resolved)
    if not get_result.get("ok"):
        return _ok(f"获取原邮件失败: {get_result.get('error')}")
    original = get_result["email"]
    subject = original.get("subject", "")
    if not subject.lower().startswith(("fwd:", "fw:")):
        subject = f"Fwd: {subject}"
    comment = args.get("comment", "")
    from_str = original.get("from", {}).get("email", "")
    fwd_header = f"\n\n---------- 转发的邮件 ----------\nFrom: {from_str}\nSubject: {original.get('subject', '')}\n\n{original.get('body_plain', '')}"
    full_body = comment + fwd_header
    mail = ComposeEmail(to=to, subject=subject, body=full_body)
    msg = build_mime_message(mail, from_email=acc.email, display_name=acc.display_name)
    try:
        smtp_config = account_to_smtp_config(acc)
        def _send():
            with SMTPClient(smtp_config) as client:
                client.send_message(msg)
        await asyncio.to_thread(_send)
        return _ok(f"转发成功!\nTo: {to}\nSubject: {subject}")
    except Exception as e:
        return _err(f"转发失败: {e}")


async def _delete_email(args: dict) -> dict:
    uids = args.get("uids", "")
    if not uids:
        return _err("缺少 uids 参数")
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    folder_alias = args.get("folder", "inbox")
    resolved, _ = _resolve_folder(acc.provider, folder_alias)
    try:
        def _del():
            with IMAPClient(config) as client:
                client.select(resolved, readonly=False)
                client.uid_store(uids, "+FLAGS", "(\\Deleted)")
                client.expunge()
        await asyncio.to_thread(_del)
        return _ok(f"邮件已删除 (UID: {uids})")
    except Exception as e:
        return _err(f"删除失败: {e}")


async def _mark_email(args: dict) -> dict:
    uids = args.get("uids", "")
    if not uids:
        return _err("缺少 uids 参数")
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    folder_alias = args.get("folder", "inbox")
    resolved, _ = _resolve_folder(acc.provider, folder_alias)
    is_read = args.get("is_read")
    is_starred = args.get("is_starred")
    if is_read is None and is_starred is None:
        return _err("请指定 is_read 或 is_starred")
    try:
        def _mark():
            with IMAPClient(config) as client:
                client.select(resolved, readonly=False)
                if is_read is not None:
                    op = "+FLAGS" if is_read else "-FLAGS"
                    client.uid_store(uids, op, "(\\Seen)")
                if is_starred is not None:
                    op = "+FLAGS" if is_starred else "-FLAGS"
                    client.uid_store(uids, op, "(\\Flagged)")
        await asyncio.to_thread(_mark)
        actions = []
        if is_read is True:
            actions.append("标为已读")
        elif is_read is False:
            actions.append("标为未读")
        if is_starred is True:
            actions.append("加星标")
        elif is_starred is False:
            actions.append("取消星标")
        return _ok(f"邮件 (UID: {uids}) {'、'.join(actions)}")
    except Exception as e:
        return _err(f"标记失败: {e}")


async def _move_email(args: dict) -> dict:
    uids = args.get("uids", "")
    target = args.get("target_folder", "")
    if not uids or not target:
        return _err("缺少 uids 或 target_folder 参数")
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    config = account_to_imap_config(acc)
    source_alias = args.get("source_folder", "inbox")
    source, _ = _resolve_folder(acc.provider, source_alias)
    target_resolved, _ = _resolve_folder(acc.provider, target)
    try:
        def _move():
            with IMAPClient(config) as client:
                client.select(source, readonly=False)
                if client.has_capability("MOVE"):
                    client.uid_move(uids, target_resolved)
                else:
                    client.uid_copy(uids, target_resolved)
                    client.uid_store(uids, "+FLAGS", "(\\Deleted)")
                    client.expunge()
        await asyncio.to_thread(_move)
        return _ok(f"邮件已从 {source_alias} 移动到 {target}")
    except Exception as e:
        return _err(f"移动失败: {e}")


async def _check_email_config(args: dict) -> dict:
    acc, err = _get_account(args.get("account_id"))
    if err:
        return _ok(err)
    preset = PROVIDER_PRESETS.get(acc.provider)
    provider_name = preset.display_name if preset else acc.provider
    return _ok(f"邮箱已配置: {acc.email} ({provider_name})")


# ── action map ────────────────────────────────────────────

_ACTION_MAP = {
    "fetch_emails": _fetch_emails,
    "search_emails": _search_emails,
    "get_email_content": _get_email_content,
    "list_folders": _list_folders,
    "send_email": _send_email,
    "reply_email": _reply_email,
    "forward_email": _forward_email,
    "delete_email": _delete_email,
    "mark_email": _mark_email,
    "move_email": _move_email,
    "check_email_config": _check_email_config,
}


# ── @tool 装饰 ──────────────────────────────────────────────

@tool(
    EMAIL_TOOL_NAME,
    (
        "邮件收发管理工具 (IMAP/SMTP). 支持收/发/搜索/标记/删除等操作. "
        "action 列表: check_email_config (检查配置) → fetch_emails (拉列表) → "
        "search_emails (按条件搜索) → get_email_content (读全文) → "
        "send_email (发邮件) → reply_email (回复) → forward_email (转发) → "
        "mark_email (标已读/星标) → delete_email (删除) → move_email (移动) → "
        "list_folders (列文件夹). "
        "参数: action (必填), folder (默认 inbox), uid, uids, to, subject, body, "
        "from_addr, since, before, unread_only, starred_only, is_read, is_starred, "
        "target_folder, source_folder, comment, limit, mark_as_read, html_body, "
        "cc, attachments, account_id."
    ),
    {
        "action": str,
        "folder": str,
        "uid": str,
        "uids": str,
        "to": str,
        "subject": str,
        "body": str,
        "from_addr": str,
        "since": str,
        "before": str,
        "unread_only": bool,
        "starred_only": bool,
        "is_read": bool,
        "is_starred": bool,
        "target_folder": str,
        "source_folder": str,
        "comment": str,
        "limit": int,
        "mark_as_read": bool,
        "html_body": str,
        "cc": str,
        "attachments": list,
        "account_id": str,
    },
)
async def _email_tool(args: dict[str, Any]) -> dict[str, Any]:
    return await _dispatch(args)


EMAIL_MCP_SERVER = create_sdk_mcp_server(
    name=EMAIL_MCP_SERVER_NAME,
    tools=[_email_tool],
)
