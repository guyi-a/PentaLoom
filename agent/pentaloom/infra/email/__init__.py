"""PentaLoom 邮件基础设施 — 存储 / 预设 / 客户端 / 编解码."""

from pentaloom.infra.email.store import (
    EmailAccount,
    EmailConfigStore,
    add_account,
    delete_account,
    get_account_by_id,
    get_default_account,
    load_accounts,
    new_account_id,
    save_accounts,
    set_default_account,
)
from pentaloom.infra.email.presets import (
    PROVIDER_PRESETS,
    IMAPConfig,
    ProviderPreset,
    SMTPConfig,
    account_to_imap_config,
    account_to_smtp_config,
)
from pentaloom.infra.email.client import IMAPClient, IMAPError, SMTPClient
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

__all__ = [
    # store
    "EmailAccount", "EmailConfigStore",
    "load_accounts", "save_accounts",
    "get_default_account", "get_account_by_id",
    "add_account", "delete_account", "set_default_account", "new_account_id",
    # presets
    "PROVIDER_PRESETS", "ProviderPreset", "SMTPConfig", "IMAPConfig",
    "account_to_smtp_config", "account_to_imap_config",
    # client
    "SMTPClient", "IMAPClient", "IMAPError",
    # codec
    "ComposeEmail", "EmailAddress", "EmailAttachment",
    "EmailMessage", "EmailSummary",
    "build_mime_message", "decode_header_value", "format_email_summary",
    "get_body_and_attachments",
    "parse_date", "parse_email_address", "parse_email_addresses",
]
