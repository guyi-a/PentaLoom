"""Settings API — 读取当前配置、写主题、浏览器连接状态.

GET  /settings              → 当前配置 (theme + version)
PATCH /settings             → 写主题 (theme: light | dark | system)
GET  /settings/connections  → 浏览器扩展连接状态
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from pentaloom import __version__
from pentaloom.config import get_settings, load_user_overrides, save_user_overrides
from pentaloom.infra.browser_bridge.registry import registry

router = APIRouter()


# ──── schemas ────────────────────────────────────────────────


class SettingsResponse(BaseModel):
    theme: str
    version: str


class PatchSettingsBody(BaseModel):
    theme: str


class BrowserSummary(BaseModel):
    browser_id: str
    label: str


class ConnectionStatus(BaseModel):
    browser_bridge_ready: bool
    browser_bridge_browsers: int
    browser_bridge_detail: list[BrowserSummary]
    email_connected: bool = False
    email_account: str | None = None


# ──── helpers ────────────────────────────────────────────────


def _current_config() -> SettingsResponse:
    """合并 pydantic-settings 默认 + settings.json 覆盖."""
    s = get_settings()
    overrides = load_user_overrides(s.data_dir)
    theme = str(overrides.get("theme", "light"))
    return SettingsResponse(
        theme=theme,
        version=f"PentaLoom v{__version__}",
    )


# ──── endpoints ──────────────────────────────────────────────


@router.get("")
async def get_settings_api() -> SettingsResponse:
    return _current_config()


@router.patch("")
async def patch_settings(body: PatchSettingsBody) -> SettingsResponse:
    """写 theme 到 settings.json, 返回更新后的完整配置."""
    if body.theme not in ("light", "dark", "system"):
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="theme must be light, dark, or system")
    s = get_settings()
    save_user_overrides(s.data_dir, {"theme": body.theme})
    return _current_config()


@router.get("/connections")
async def get_connections() -> ConnectionStatus:
    sessions = registry.list_sessions()
    browsers = [s for s in sessions if s.browser_id]

    # 邮箱连接状态
    from pentaloom.infra.email import get_default_account
    s = get_settings()
    email_acc = get_default_account(s.data_dir)

    return ConnectionStatus(
        browser_bridge_ready=len(browsers) > 0,
        browser_bridge_browsers=len(browsers),
        browser_bridge_detail=[
            BrowserSummary(browser_id=b.browser_id, label=b.label)
            for b in browsers
        ],
        email_connected=email_acc is not None,
        email_account=email_acc.email if email_acc else None,
    )