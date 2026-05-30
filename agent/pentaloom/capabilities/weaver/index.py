"""weaver/index.json 的读 / 写 / append / 删, 以及 skills/<name>/ → .claude/skills/<name>/ symlink 同步.

为啥 index.json 跟 file system 双轨:
- file system 是 source of truth (用户 vscode 直接看)
- index.json 是给主 agent 一次性看清单的轻量索引
- 每次 weave / edit / delete 都同步更新两侧
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

from pentaloom.capabilities.weaver import paths
from pentaloom.capabilities.weaver.models import (
    IndexEntry,
    WeaverIndex,
    WeaverKind,
)
from pentaloom.config import Settings


class WeaverError(Exception):
    """weaver 业务错误 (写盘 / index 解析 / 名字冲突 / SDK rebuild).

    由 tools/weaver.py 捕获后转成 LLM-readable 的 is_error 帧.
    """


def load_index(settings: Settings) -> WeaverIndex:
    """读 weaver/index.json. 不存在返空 index (首次启动)."""
    p = paths.index_json(settings)
    if not p.exists():
        return WeaverIndex()
    try:
        return WeaverIndex.model_validate_json(p.read_text())
    except Exception as e:
        # 损坏的 index 不该 crash 整个进程 — 警告 + 当空 index 处理.
        # 后续 weave_* 会重新 append, 损坏数据被覆盖.
        logger.warning(f"weaver/index.json 损坏, 当空 index 处理: {e}")
        return WeaverIndex()


def save_index(settings: Settings, index: WeaverIndex) -> None:
    paths.ensure_dirs(settings)
    paths.index_json(settings).write_text(
        index.model_dump_json(indent=2) + "\n"
    )


def upsert_entry(settings: Settings, entry: IndexEntry) -> WeaverIndex:
    """新增或替换 (按 name) 单条 entry. 返回更新后的 index."""
    index = load_index(settings)
    bucket = index.bucket(entry.kind)
    bucket[:] = [e for e in bucket if e.name != entry.name]
    bucket.append(entry)
    save_index(settings, index)
    return index


def remove_entry(settings: Settings, kind: WeaverKind, name: str) -> WeaverIndex:
    index = load_index(settings)
    bucket = index.bucket(kind)
    bucket[:] = [e for e in bucket if e.name != name]
    save_index(settings, index)
    return index


def find_entry(
    settings: Settings, kind: WeaverKind, name: str
) -> IndexEntry | None:
    index = load_index(settings)
    for e in index.bucket(kind):
        if e.name == name:
            return e
    return None


def name_exists_any_kind(settings: Settings, name: str) -> WeaverKind | None:
    """名字冲突检查 — 跨 kind 不允许重名. 返回占用方 kind 或 None."""
    index = load_index(settings)
    for kind in ("skill", "subagent", "workflow", "app"):
        for e in index.bucket(kind):  # type: ignore[arg-type]
            if e.name == name:
                return kind  # type: ignore[return-value]
    return None


def sync_skill_symlink(settings: Settings, name: str) -> None:
    """weaver/skills/<name>/ → data_dir/.claude/skills/<name>/. 幂等.

    macOS / Linux 用 os.symlink; Windows 退化为 copytree (M15 Electron 实施时再考虑).
    """
    src = paths.skill_dir(settings, name)
    dst = paths.skill_symlink(settings, name)
    if not src.exists():
        raise WeaverError(f"skill {name} 物理目录不存在: {src}")
    if dst.is_symlink() or dst.exists():
        return  # 幂等
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(src, dst, target_is_directory=True)
    except OSError as e:
        raise WeaverError(f"无法 symlink {src} → {dst}: {e}") from e


def remove_skill_symlink(settings: Settings, name: str) -> None:
    dst = paths.skill_symlink(settings, name)
    if dst.is_symlink() or dst.exists():
        try:
            dst.unlink()
        except OSError as e:
            logger.warning(f"删 skill symlink 失败 {dst}: {e}")


def resolve_product_path(settings: Settings, entry: IndexEntry) -> Path:
    """index entry.path 是相对 weaver/, 这里转绝对路径."""
    return paths.weaver_root(settings) / entry.path
