"""settings.json 持久化 — 目前只存 theme.

读: 优先读 settings.json, 缺字段回退默认值.
写: 原子替换 (write tmp → rename).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

_WRITABLE_KEYS = frozenset({"theme"})


def settings_json_path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def load_user_overrides(data_dir: Path) -> dict[str, Any]:
    """读 settings.json, 不存在或损坏返空 dict."""
    path = settings_json_path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = path.read_text("utf-8")
        return json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"settings.json unreadable, skipping: {exc}")
        return {}


def save_user_overrides(data_dir: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """合并 patch 写 settings.json (原子替换), 返回写入后的完整 overrides."""
    current = load_user_overrides(data_dir)
    applied = {**current}
    for k, v in patch.items():
        if k in _WRITABLE_KEYS:
            applied[k] = v
    cleaned = {k: v for k, v in applied.items() if v is not None and v != ""}

    path = settings_json_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", "utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.error(f"settings.json write failed: {exc}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return cleaned
