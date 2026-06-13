"""Email API — 邮箱账号配置.

GET  /email/providers      → 可用服务商列表
GET  /email/accounts       → 已配置账号列表
POST /email/accounts       → 新增账号 (SMTP 验证后存盘)
DELETE /email/accounts/{id} → 删除账号
POST /email/accounts/{id}/test → 发测试邮件
"""
from __future__ import annotations

import asyncio
import smtplib

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, field_validator

from pentaloom.config import get_settings
from pentaloom.infra.email import (
    PROVIDER_PRESETS,
    EmailAccount,
    SMTPClient,
    account_to_smtp_config,
    add_account,
    delete_account,
    get_account_by_id,
    load_accounts,
    new_account_id,
)
from pentaloom.infra.email.codec import ComposeEmail, build_mime_message

router = APIRouter()


# ──── schemas ────────────────────────────────────────────────


class ProviderInfo(BaseModel):
    id: str
    display_name: str
    email_suffix: str


class ProviderListResponse(BaseModel):
    providers: list[ProviderInfo]


class AccountInput(BaseModel):
    provider: str
    email: str
    password: str
    display_name: str | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if v not in PROVIDER_PRESETS:
            valid = ", ".join(sorted(PROVIDER_PRESETS))
            raise ValueError(f"Invalid provider: {v}. Must be one of: {valid}")
        return v


class AccountResponse(BaseModel):
    id: str
    provider: str
    email: str
    display_name: str | None = None
    is_default: bool = False
    # 注意: 不返回密码


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    default_account_id: str | None = None


class MutationResult(BaseModel):
    ok: bool
    message: str
    error_code: str | None = None
    account_id: str | None = None
    email: str | None = None


class TestEmailResult(BaseModel):
    ok: bool
    message: str
    error_code: str | None = None


# ──── helpers ────────────────────────────────────────────────


def _data_dir():
    return get_settings().data_dir


def _account_to_response(acc: EmailAccount, default_id: str | None) -> AccountResponse:
    return AccountResponse(
        id=acc.id,
        provider=acc.provider,
        email=acc.email,
        display_name=acc.display_name or None,
        is_default=acc.id == default_id,
    )


async def _validate_smtp(account: EmailAccount) -> MutationResult:
    """尝试 SMTP 连接验证账号, 失败返 ok=False."""
    try:
        config = account_to_smtp_config(account)
    except ValueError as e:
        return MutationResult(ok=False, message=str(e), error_code="INVALID_PROVIDER")

    def _connect():
        with SMTPClient(config):
            pass  # 连接成功 = 验证通过

    try:
        await asyncio.to_thread(_connect)
        return MutationResult(ok=True, message="SMTP validation passed")
    except smtplib.SMTPAuthenticationError:
        return MutationResult(
            ok=False,
            message="认证失败，请检查邮箱地址和授权码",
            error_code="AUTH_FAILED",
        )
    except smtplib.SMTPException as e:
        return MutationResult(ok=False, message=f"SMTP 错误: {e}", error_code="SMTP_ERROR")
    except Exception as e:
        return MutationResult(ok=False, message=f"连接失败: {e}", error_code="CONNECTION_ERROR")


# ──── endpoints ──────────────────────────────────────────────


@router.get("/providers")
async def get_providers() -> ProviderListResponse:
    providers = [
        ProviderInfo(id=k, display_name=p.display_name, email_suffix=p.email_suffix)
        for k, p in PROVIDER_PRESETS.items()
    ]
    return ProviderListResponse(providers=providers)


@router.get("/accounts")
async def list_accounts() -> AccountListResponse:
    store = load_accounts(_data_dir())
    return AccountListResponse(
        accounts=[_account_to_response(a, store.accounts[0].id if store.accounts else None) for a in store.accounts],
        default_account_id=store.accounts[0].id if store.accounts else None,
    )


@router.post("/accounts")
async def create_account(body: AccountInput) -> MutationResult:
    account = EmailAccount(
        id=new_account_id(),
        provider=body.provider,
        email=body.email,
        display_name=body.display_name or "",
        password=body.password,
    )
    # 先验证 SMTP
    result = await _validate_smtp(account)
    if not result.ok:
        return result

    saved = add_account(_data_dir(), account)
    return MutationResult(
        ok=True,
        message="邮箱账号添加成功",
        account_id=saved.id,
        email=body.email,
    )


@router.delete("/accounts/{account_id}")
async def delete_account_endpoint(account_id: str) -> MutationResult:
    if not get_account_by_id(_data_dir(), account_id):
        raise HTTPException(status_code=404, detail="Account not found")
    delete_account(_data_dir(), account_id)
    return MutationResult(ok=True, message="邮箱账号已删除")


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: str) -> TestEmailResult:
    account = get_account_by_id(_data_dir(), account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # 构建测试邮件
    preset = PROVIDER_PRESETS.get(account.provider)
    provider_name = preset.display_name if preset else account.provider
    mail = ComposeEmail(
        to=account.email,
        subject=f"PentaLoom {provider_name} 测试",
        body=(
            f"这是来自 PentaLoom 的测试邮件，用于验证您的 {provider_name} 配置是否正常工作。\n\n"
            f"This is a test email from PentaLoom to verify your {provider_name} configuration."
        ),
    )
    msg = build_mime_message(mail, from_email=account.email, display_name=account.display_name)

    try:
        config = account_to_smtp_config(account)
        def _send():
            with SMTPClient(config) as client:
                client.send_message(msg)
        await asyncio.to_thread(_send)
        return TestEmailResult(ok=True, message=f"测试邮件已发送至 {account.email}")
    except smtplib.SMTPAuthenticationError:
        return TestEmailResult(ok=False, message="认证失败", error_code="AUTH_FAILED")
    except Exception as e:
        return TestEmailResult(ok=False, message=f"发送失败: {e}", error_code="SEND_ERROR")
