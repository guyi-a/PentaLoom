"""邮箱服务商预设 — Gmail + QQ.

每个 preset 包含 SMTP + IMAP 的 host/port/SSL 配置.
"""
from __future__ import annotations

from pydantic import BaseModel


class ProviderPreset(BaseModel):
    id: str
    display_name: str
    email_suffix: str
    smtp_host: str
    smtp_port: int
    smtp_use_ssl: bool = True
    imap_host: str
    imap_port: int
    imap_use_ssl: bool = True


class SMTPConfig(BaseModel):
    host: str
    port: int
    use_ssl: bool = True
    username: str
    password: str
    display_name: str = ""


class IMAPConfig(BaseModel):
    host: str
    port: int
    use_ssl: bool = True
    username: str
    password: str


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "gmail": ProviderPreset(
        id="gmail",
        display_name="Gmail",
        email_suffix="@gmail.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_use_ssl=True,
        imap_host="imap.gmail.com",
        imap_port=993,
        imap_use_ssl=True,
    ),
    "qq": ProviderPreset(
        id="qq",
        display_name="QQ邮箱",
        email_suffix="@qq.com",
        smtp_host="smtp.qq.com",
        smtp_port=465,
        smtp_use_ssl=True,
        imap_host="imap.qq.com",
        imap_port=993,
        imap_use_ssl=True,
    ),
}


def account_to_smtp_config(account: "EmailAccount") -> SMTPConfig:
    """从 EmailAccount + preset 构建 SMTPConfig."""
    preset = PROVIDER_PRESETS.get(account.provider)
    if not preset:
        raise ValueError(f"Unknown provider: {account.provider}")
    return SMTPConfig(
        host=preset.smtp_host,
        port=preset.smtp_port,
        use_ssl=preset.smtp_use_ssl,
        username=account.email,
        password=account.password,
        display_name=account.display_name,
    )


def account_to_imap_config(account: "EmailAccount") -> IMAPConfig:
    """从 EmailAccount + preset 构建 IMAPConfig."""
    preset = PROVIDER_PRESETS.get(account.provider)
    if not preset:
        raise ValueError(f"Unknown provider: {account.provider}")
    return IMAPConfig(
        host=preset.imap_host,
        port=preset.imap_port,
        use_ssl=preset.imap_use_ssl,
        username=account.email,
        password=account.password,
    )
