"""Invocable App 业务 logic — weave / read / validate / 软删. 跟 skill.py 同款风格.

详见 docs/app-invocable-exploration.md v3 + docs/pentaloom-app-generation-plan.md.

两层 schema 严格分开:
  app.json (AppDefinition)        — runtime declaration: 5 类组件怎么跑
  manifest.json (InvocableAppManifest) — invocable contract: agent 怎么调

命名 PentaLoom-native (不抄 Krow 品牌词): schedules / AppDefinition / files/.

文件布局:
  weaver/apps/<name>/
  ├── app.json         AppDefinition (可选, M16 Phase A 不写也行; Phase B+ runtime 需要)
  ├── manifest.json    InvocableAppManifest (必填)
  ├── meta.json        runtime 元数据 (created_at / use_count / is_trusted)
  ├── files/           app 源码根目录 (windows/services/scripts/assets/...)
  ├── runs/<run_id>/   invocation 输出 (artifact_path 引)
  └── logs/            runtime 日志
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from pentaloom.capabilities.weaver import index, paths
from pentaloom.capabilities.weaver.models import (
    AppDefinition,
    IndexEntry,
    InvocableAppManifest,
    InvocableAppMeta,
    InvocationSpec,
    WeaverSource,
)
from pentaloom.config import Settings

_VALID_NAME = "abcdefghijklmnopqrstuvwxyz0123456789-"

# 系统维护文件 — agent 写 files 时不许覆盖.
_RESERVED_TOP_LEVEL = frozenset({"manifest.json", "app.json", "meta.json"})


def _validate_name(name: str) -> str:
    """app name 必须 kebab-case. 跟 skill 同款 — 跨 kind 命名空间统一."""
    name = name.strip()
    if not name:
        raise index.WeaverError("app name 不能为空")
    if not all(c in _VALID_NAME for c in name):
        raise index.WeaverError(
            f"app name 只能小写字母 / 数字 / 短横线: {name!r}"
        )
    if len(name) > 64:
        raise index.WeaverError(f"app name 太长 (>64): {name!r}")
    return name


def _validate_relative_path(rel_path: str, *, label: str) -> str:
    """禁 path traversal. POSIX 风格, 相对路径."""
    rel_path = rel_path.strip()
    if not rel_path:
        raise index.WeaverError(f"{label} path 不能为空")
    if rel_path.startswith("/") or ".." in rel_path.split("/"):
        raise index.WeaverError(f"{label} path 不允许跳出 app 目录: {rel_path!r}")
    return rel_path


# ─── manifest.json (InvocableAppManifest) ───────────────────────────────────

def parse_manifest(text: str) -> InvocableAppManifest:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise index.WeaverError(f"manifest.json 不是合法 JSON: {e}") from e
    try:
        return InvocableAppManifest.model_validate(data)
    except Exception as e:
        raise index.WeaverError(f"manifest schema 校验失败: {e}") from e


def _validate_manifest_invocations(manifest: InvocableAppManifest) -> None:
    """manifest 自洽 — invocation id 唯一, schema 是 dict."""
    if not manifest.invocations:
        raise index.WeaverError(
            "manifest 必须至少声明一个 invocation; 否则 agent 没法 invoke_app, "
            "等于一个孤岛 UI app, 应该写 markdown 而不是 weave_app"
        )
    seen_ids: set[str] = set()
    for inv in manifest.invocations:
        if inv.id in seen_ids:
            raise index.WeaverError(f"invocation id 重复: {inv.id!r}")
        seen_ids.add(inv.id)
        if not isinstance(inv.input_schema, dict):
            raise index.WeaverError(
                f"invocation {inv.id} input_schema 必须是 JSON Schema object"
            )
        if not isinstance(inv.output_schema, dict):
            raise index.WeaverError(
                f"invocation {inv.id} output_schema 必须是 JSON Schema object"
            )


# ─── app.json (AppDefinition) — 可选, M16 Phase A 不强制 ───────────────────

def parse_app_definition(text: str) -> AppDefinition:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise index.WeaverError(f"app.json 不是合法 JSON: {e}") from e
    try:
        return AppDefinition.model_validate(data)
    except Exception as e:
        raise index.WeaverError(f"app.json schema 校验失败: {e}") from e


def _validate_app_definition(app_def: AppDefinition) -> None:
    """component name 在同 kind 内唯一; 不强制必须有 component (空 app 占位 OK)."""
    for kind, comps in (
        ("window", app_def.components.windows),
        ("service", app_def.components.services),
        ("script", app_def.components.scripts),
        ("schedule", app_def.components.schedules),
        ("watch", app_def.components.watches),
    ):
        seen: set[str] = set()
        for c in comps:
            if c.name in seen:
                raise index.WeaverError(
                    f"app.json {kind} name 重复: {c.name!r}"
                )
            seen.add(c.name)


# ─── manifest ↔ app.json 一致性 (跨文件校验) ──────────────────────────────

def _validate_invocation_targets(
    manifest: InvocableAppManifest, app_def: AppDefinition | None
) -> None:
    """invocation.target 必须指向 app.json 里存在的 component.

    app_def is None (Phase A 只写 manifest, 没 app.json) 时, target 缺省也 OK —
    放过去, Phase B+ runtime 起来再卡校验. 但 target 在 manifest 里**写了**就要校.
    """
    if app_def is None:
        # Phase A: target 必填字段是否填了, 不验是否指向 component (没 app.json 比啥).
        # 但 invocation.target 现在是 Optional, 完全省略也 OK (M16 第一版宽松).
        return

    by_kind: dict[str, set[str]] = {
        "window": {c.name for c in app_def.components.windows},
        "service": {c.name for c in app_def.components.services},
        "script": {c.name for c in app_def.components.scripts},
    }
    for inv in manifest.invocations:
        if inv.target is None:
            raise index.WeaverError(
                f"invocation {inv.id} 缺 target (app.json 有 components 时 target 必填)"
            )
        if inv.target.component not in by_kind:
            raise index.WeaverError(
                f"invocation {inv.id} target.component={inv.target.component!r} "
                "不合法 (必须是 window/service/script)"
            )
        if inv.target.name not in by_kind[inv.target.component]:
            raise index.WeaverError(
                f"invocation {inv.id} target → {inv.target.component}/{inv.target.name} "
                f"不存在 (app.json {inv.target.component}s 里只有: "
                f"{sorted(by_kind[inv.target.component])})"
            )


# ─── 写盘核心 ──────────────────────────────────────────────────────────────

def _write_files_tree(settings: Settings, name: str, files: dict[str, str]) -> None:
    """写 LLM 给的多文件源码到 files/ 子树. 拦 path traversal + 拦覆盖系统文件."""
    root = paths.app_dir(settings, name)
    files_root = paths.app_files_dir(settings, name)
    for rel_path, content in files.items():
        safe_rel = _validate_relative_path(rel_path, label="file")
        # 顶层路径校验: 不能覆盖 manifest/app/meta. 也不能落到 runs/ logs/ 之类系统目录.
        first = safe_rel.split("/", 1)[0]
        if first in _RESERVED_TOP_LEVEL:
            raise index.WeaverError(
                f"files 不能覆盖系统维护文件: {safe_rel}"
            )
        if first in {"runs", "logs"}:
            raise index.WeaverError(
                f"files 不能写入 runs/ 或 logs/ (系统目录): {safe_rel}"
            )
        # 默认所有 files 落到 files/ 子目录, 跟 plan §3 一致
        # (如果 rel_path 本身就以 'files/' 开头, 不要重复)
        if safe_rel.startswith("files/"):
            target = root / safe_rel
        else:
            target = files_root / safe_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def weave_app(
    settings: Settings,
    name: str,
    description: str,
    manifest_json: str,
    files: dict[str, str],
    *,
    app_json: str | None = None,
    source: WeaverSource = "agent_woven",
) -> InvocableAppMeta:
    """织一个 invocable app. 接口跟 plan §7.1 对齐.

    必填: name / description / manifest_json (含至少 1 个 invocation) / files
    可选: app_json (Phase A 不写也行; Phase B+ runtime 需要)

    跟 weave_skill 同款流程: 校验 → 名字冲突 check → 写盘 → 加 index.
    """
    name = _validate_name(name)
    description = description.strip()
    if not description:
        raise index.WeaverError("description 不能为空")
    if not files:
        raise index.WeaverError("files 不能为空 — app 至少要 1 个源码文件")

    manifest = parse_manifest(manifest_json)
    if manifest.name != name:
        raise index.WeaverError(
            f"name 跟 manifest.name 不一致: arg={name!r} manifest={manifest.name!r}"
        )
    if manifest.description.strip() != description:
        raise index.WeaverError(
            "description 跟 manifest.description 不一致 — 两处应写同一句"
        )
    _validate_manifest_invocations(manifest)

    app_def: AppDefinition | None = None
    if app_json is not None and app_json.strip():
        app_def = parse_app_definition(app_json)
        if app_def.name != name:
            raise index.WeaverError(
                f"name 跟 app.json.name 不一致: arg={name!r} app={app_def.name!r}"
            )
        _validate_app_definition(app_def)

    # 跨文件: target → component 存在性 (只在有 app.json 时验)
    _validate_invocation_targets(manifest, app_def)

    # 冲突检查: 跨 kind 不重名 + 跟内置 skill 也不重名
    occupied = index.name_exists_any_kind(settings, name)
    if occupied is not None:
        raise index.WeaverError(
            f"名字已被占用 (kind={occupied}, name={name!r}). 换个名字"
        )
    from pentaloom.prompts.skills import ENABLED_SKILLS
    if name in ENABLED_SKILLS:
        raise index.WeaverError(
            f"名字 {name!r} 跟内置 skill 冲突. 换个名字"
        )

    # ─── 写盘 ───
    paths.ensure_app_dirs(settings, name)
    paths.app_manifest(settings, name).write_text(
        manifest.model_dump_json(indent=2) + "\n"
    )
    if app_def is not None:
        paths.app_definition(settings, name).write_text(
            app_def.model_dump_json(indent=2, exclude_none=True) + "\n"
        )
    _write_files_tree(settings, name, files)

    meta = InvocableAppMeta(
        name=name, description=description, source=source,
        created_at=datetime.utcnow(),
    )
    paths.app_meta(settings, name).write_text(meta.model_dump_json(indent=2))

    index.upsert_entry(
        settings,
        IndexEntry(
            name=name, kind="app", description=description,
            path=f"apps/{name}/", source=source,
        ),
    )
    logger.info(
        f"weaved app: {name} ({len(manifest.invocations)} invocations, "
        f"{len(files)} files, app.json={'yes' if app_def else 'no'})"
    )
    return meta


def read_manifest(settings: Settings, name: str) -> InvocableAppManifest:
    p = paths.app_manifest(settings, name)
    if not p.exists():
        raise index.WeaverError(f"app manifest 不存在: {p}")
    return parse_manifest(p.read_text())


def read_meta(settings: Settings, name: str) -> InvocableAppMeta | None:
    p = paths.app_meta(settings, name)
    if not p.exists():
        return None
    return InvocableAppMeta.model_validate_json(p.read_text())


def read_app_definition(settings: Settings, name: str) -> AppDefinition | None:
    p = paths.app_definition(settings, name)
    if not p.exists():
        return None
    return parse_app_definition(p.read_text())


def list_app_files(settings: Settings, name: str) -> list[str]:
    """递归列 files/ 下所有文件, 给 inspect 用 (避免返完整源码太大)."""
    files_root = paths.app_files_dir(settings, name)
    if not files_root.exists():
        return []
    return sorted(
        str(p.relative_to(files_root))
        for p in files_root.rglob("*")
        if p.is_file()
    )


def read_app_file(settings: Settings, name: str, rel_path: str) -> str:
    """读 files/ 下某文件全文 (给 agent 按需 fetch)."""
    safe_rel = _validate_relative_path(rel_path, label="file")
    target = paths.app_files_dir(settings, name) / safe_rel
    if not target.exists():
        raise index.WeaverError(f"app file 不存在: {rel_path}")
    return target.read_text()


def delete_app_soft(settings: Settings, name: str) -> Path:
    """软删: 整个 apps/<name>/ 搬到 weaver/.trash/."""
    name = _validate_name(name)
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise index.WeaverError(f"app 不存在: {name}")

    src = paths.app_dir(settings, name)
    if not src.exists():
        raise index.WeaverError(f"app 物理目录已丢: {src}")

    index.remove_entry(settings, "app", name)
    paths.trash_dir(settings).mkdir(parents=True, exist_ok=True)
    dst = paths.trash_target(settings, "app", name)
    shutil.move(str(src), str(dst))
    logger.info(f"deleted (soft) app: {name} → {dst}")
    return dst


def manifest_invocations_summary(settings: Settings, name: str) -> dict[str, Any]:
    """给 inspect_weaver / list_weaver 用 — 不返 schema 全文 (太长), 返每个
    invocation 的 id + target + input/output 字段名 + components 计数."""
    m = read_manifest(settings, name)
    app_def = read_app_definition(settings, name)
    out: dict[str, Any] = {
        "name": m.name,
        "type": m.type,
        "version": m.version,
        "invocations": [_invocation_summary(inv) for inv in m.invocations],
        "permissions": m.permissions.model_dump(),
        "files": list_app_files(settings, name),
    }
    if app_def is not None:
        out["components"] = {
            "windows": [c.name for c in app_def.components.windows],
            "services": [c.name for c in app_def.components.services],
            "scripts": [c.name for c in app_def.components.scripts],
            "schedules": [c.name for c in app_def.components.schedules],
            "watches": [c.name for c in app_def.components.watches],
        }
    return out


def _invocation_summary(inv: InvocationSpec) -> dict[str, Any]:
    return {
        "id": inv.id,
        "description": inv.description,
        "target": inv.target.model_dump() if inv.target else None,
        "input_keys": list((inv.input_schema.get("properties") or {}).keys()),
        "output_keys": list((inv.output_schema.get("properties") or {}).keys()),
        "timeout_ms": inv.timeout_ms,
        "example_count": len(inv.examples),
    }
