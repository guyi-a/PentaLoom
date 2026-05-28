"""session 数据落盘的路径常量 + session 名生成.

约定:
  <sandbox>/.pentaloom/browser-use/session.json          — 落盘的 session 配置
  <sandbox>/.pentaloom/browser-use/session.cookies.json  — 自动导出的 cookies

每个 PentaLoom session 沙箱独占一套 — 不需要 conv-{id} 前缀, 因为 sandbox 路径
本身就已经按 sid 隔离了 (settings.sandbox_dir_for(sid)).

session 名 (传给 browser-use CLI 的 --session 值) 必须**跨所有 PentaLoom 会话**
全局唯一, 因为 browser-use CLI 在自己的 state 里以这个名字管后台浏览器进程,
两个不同 PentaLoom 会话用同名 session 会复用同一个浏览器, 撞车. sid 本身是
UUID, 直接拿来做 session 名足够.
"""

from __future__ import annotations

import json
from pathlib import Path

from pentaloom.capabilities.browser._models import StoredSessionConfig

BROWSER_USE_SUBDIR = ".pentaloom/browser-use"
SESSION_CONFIG_FILE = "session.json"
SESSION_COOKIES_FILE = "session.cookies.json"
SESSION_NAME_PREFIX = "pl"


def compute_session_name(sid: str) -> str:
    """Session 名 = pl-{sid}. CLI 标识符限定字符宽松, sid 全是 UUID hex+dash, 安全."""
    return f"{SESSION_NAME_PREFIX}-{sid}"


def session_data_dir(sandbox: Path) -> Path:
    return sandbox / BROWSER_USE_SUBDIR


def session_config_path(sandbox: Path) -> Path:
    return session_data_dir(sandbox) / SESSION_CONFIG_FILE


def session_cookies_path(sandbox: Path) -> Path:
    return session_data_dir(sandbox) / SESSION_COOKIES_FILE


def load_session_config(sandbox: Path) -> StoredSessionConfig:
    """读 session.json. 文件不存在 / 解析失败 → 返回空 config (不抛)."""
    path = session_config_path(sandbox)
    if not path.exists():
        return StoredSessionConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return StoredSessionConfig()
    if not isinstance(data, dict):
        return StoredSessionConfig()
    # 过滤未知字段, Pydantic v2 默认 extra=ignore, 但显式更稳
    allowed = StoredSessionConfig.model_fields.keys()
    clean = {k: v for k, v in data.items() if k in allowed}
    try:
        return StoredSessionConfig(**clean)
    except Exception:
        return StoredSessionConfig()


def save_session_config(sandbox: Path, config: StoredSessionConfig) -> None:
    path = session_config_path(sandbox)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.model_dump(), ensure_ascii=False),
        encoding="utf-8",
    )


def clear_session_config(sandbox: Path) -> None:
    """目前没用上 (close 后 session 配置故意保留, 方便下次 open 重放). 留着以备 reset."""
    path = session_config_path(sandbox)
    path.unlink(missing_ok=True)
