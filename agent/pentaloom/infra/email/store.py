"""邮箱配置存储.

数据文件: <data_dir>/email_accounts.json
结构: {"accounts": [EmailAccount, ...]}
文件权限 0600 — 授权码明文存本地.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel

# ──── 类型 ──────────────────────────────────────────────────


class EmailAccount(BaseModel):
    id: str
    provider: str  # "gmail" | "qq"
    email: str
    display_name: str = ""
    password: str  # 授权码
    is_default: bool = False


class EmailConfigStore(BaseModel):
    accounts: list[EmailAccount] = []


# ──── 存储 ──────────────────────────────────────────────────


def _accounts_path(data_dir: Path) -> Path:
    return data_dir / "email_accounts.json"


def load_accounts(data_dir: Path) -> EmailConfigStore:
    """读取邮箱配置, 不存在或损坏返空 store."""
    path = _accounts_path(data_dir)
    if not path.is_file():
        return EmailConfigStore()
    try:
        raw = json.loads(path.read_text("utf-8"))
        return EmailConfigStore.model_validate(raw)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning(f"email_accounts.json unreadable, skipping: {exc}")
        return EmailConfigStore()


def save_accounts(data_dir: Path, store: EmailConfigStore) -> None:
    """原子写入 email_accounts.json, 权限 0600."""
    path = _accounts_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(store.model_dump_json(indent=2) + "\n", "utf-8")
    # 权限 0600 (授权码明文)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


# ──── CRUD ──────────────────────────────────────────────────


def get_default_account(data_dir: Path) -> EmailAccount | None:
    store = load_accounts(data_dir)
    for acc in store.accounts:
        if acc.is_default:
            return acc
    return store.accounts[0] if store.accounts else None


def get_account_by_id(data_dir: Path, account_id: str) -> EmailAccount | None:
    store = load_accounts(data_dir)
    for acc in store.accounts:
        if acc.id == account_id:
            return acc
    return None


def add_account(data_dir: Path, account: EmailAccount) -> EmailAccount:
    """添加账号, 第一个自动设为 default."""
    store = load_accounts(data_dir)
    if not store.accounts:
        account.is_default = True
    # 去重: 同 provider + 同 email 只保留最新
    store.accounts = [
        a for a in store.accounts
        if not (a.provider == account.provider and a.email == account.email)
    ]
    store.accounts.append(account)
    save_accounts(data_dir, store)
    return account


def delete_account(data_dir: Path, account_id: str) -> bool:
    """删除账号, 若删的是 default 则提升下一个为 default."""
    store = load_accounts(data_dir)
    before = len(store.accounts)
    store.accounts = [a for a in store.accounts if a.id != account_id]
    if len(store.accounts) == before:
        return False
    # 若删的是 default, 提升第一个
    if not any(a.is_default for a in store.accounts) and store.accounts:
        store.accounts[0].is_default = True
    save_accounts(data_dir, store)
    return True


def set_default_account(data_dir: Path, account_id: str) -> bool:
    store = load_accounts(data_dir)
    found = False
    for acc in store.accounts:
        if acc.id == account_id:
            acc.is_default = True
            found = True
        else:
            acc.is_default = False
    if found:
        save_accounts(data_dir, store)
    return found


def new_account_id() -> str:
    return str(uuid.uuid4())
