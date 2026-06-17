"""Invocable App 业务逻辑: 创建、读写、校验、finalize 与软删.

两层 schema 严格分开:
  app.json (AppDefinition)        — runtime declaration: 5 类组件怎么跑
  manifest.json (InvocableAppManifest) — invocable contract: agent 怎么调

文件布局:
  weaver/apps/<name>/
  ├── app.json         AppDefinition, 描述组件如何运行
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
    """禁 path traversal. POSIX 风格, 相对路径.

    字符串层面拒明显穿越. resolve 后再二次校验 (`_resolve_within_files`) 才是
    最终防御 — 单靠字符串挡不住符号链接 / 大小写差异 / NUL 字节等.
    """
    rel_path = rel_path.strip()
    if not rel_path:
        raise index.WeaverError(f"{label} path 不能为空")
    if rel_path.startswith("/") or ".." in rel_path.split("/"):
        raise index.WeaverError(f"{label} path 不允许跳出 app 目录: {rel_path!r}")
    if "\x00" in rel_path:
        raise index.WeaverError(f"{label} path 含非法字符 (NUL)")
    return rel_path


def _resolve_within_files(
    files_root: Path, rel_path: str, *, label: str
) -> Path:
    """最终目标必须 resolve().is_relative_to(files_root.resolve()), 不要只靠字符串.

    防 symlink / .. 残留 / 大小写规范化绕过.
    """
    target = (files_root / rel_path).resolve()
    files_resolved = files_root.resolve()
    try:
        target.relative_to(files_resolved)
    except ValueError as e:
        raise index.WeaverError(
            f"{label} path {rel_path!r} 越出 files/ 根 (路径穿越)"
        ) from e
    return target


def _schedule_trigger_action(action: str, settings: Settings, name: str) -> None:
    """同步调 launchd_plist — 写 plist + launchctl load/unload.

    schedule / watch / service 三类都走独立 launchd plist, 由系统接管生命周期.

    action:
      - 'reload' (finalize 成功): 重新生成 + load app 的所有 plist
      - 'stop' (finalize 失败 + delete): unload + 删 app 的所有 plist

    不吞异常: finalize 需要感知 plist reload 失败并转为 failed; delete 阶段再自行容错.
    """
    from pentaloom.capabilities.weaver import launchd_plist
    if action == "reload":
        launchd_plist.reload_for_app(name, settings)
    elif action == "stop":
        launchd_plist.unload_for_app(name)


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


# ─── app.json (AppDefinition) ───────────────────────────────────────────────

def parse_app_definition(text: str) -> AppDefinition:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise index.WeaverError(f"app.json 不是合法 JSON: {e}") from e
    try:
        return AppDefinition.model_validate(data)
    except Exception as e:
        # pydantic ValidationError 翻成人话: 命中已知常见错优先, 否则透传.
        from pydantic import ValidationError
        if isinstance(e, ValidationError):
            hints: list[str] = []
            for err in e.errors():
                loc = ".".join(str(p) for p in err.get("loc", ()))
                msg = err.get("msg", "")
                etype = err.get("type", "")
                # 最常见: schedules/watches 漏 invocation_id
                if (
                    etype == "missing"
                    and "invocation_id" in loc
                    and ("schedules" in loc or "watches" in loc)
                ):
                    hints.append(
                        f"  · {loc}: 缺 invocation_id 字段. schedule/watch 每项必须含 "
                        "invocation_id (引用 manifest.invocations[].id 决定触发哪个 "
                        "invocation). watch 不触发时设 None 但字段不能漏."
                    )
                else:
                    hints.append(f"  · {loc}: {msg} (type={etype})")
            raise index.WeaverError(
                f"app.json schema 校验失败:\n" + "\n".join(hints)
            ) from e
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

    app_def is None 时缺少组件定义, 无法校验 target 是否存在;
    但 target 只要写了 component/name, 就必须能在 app.json 中找到.
    """
    if app_def is None:
        # 兼容旧 app: 没有 app.json 时不做跨文件 target 校验.
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
    files: dict[str, str] | None = None,
    *,
    app_json: str | None = None,
    source: WeaverSource = "agent_woven",
) -> InvocableAppMeta:
    """织一个 invocable app 骨架.

    必填: name / description / manifest_json
    可选: app_json (没 app.json 后续 invoke_app 会拒); files (空 dict / None = 只建骨架)

    新流程 (取代之前的 atomic):
      weave_app(...)            ← 这步 HITL 一次, status=draft (空 files 也 OK)
      weave_app_write_file(...) ← 多次, auto-pass, status 保持 draft
      weave_app_finalize(...)   ← auto-pass, 4 项校验通过 → status=ready
      invoke_app(...)           ← 只允许 status=ready

    files 参数保留向后兼容 — atomic 风格的 caller (spike / 测试) 仍可一把传完.
    传了 files 也只是写盘, status 仍是 draft, 用户必须显式 finalize 才能 invoke.
    """
    name = _validate_name(name)
    description = description.strip()
    if not description:
        raise index.WeaverError("description 不能为空")
    files = files or {}

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
    if files:
        _write_files_tree(settings, name, files)

    now = datetime.utcnow()
    meta = InvocableAppMeta(
        name=name, description=description, source=source,
        status="draft", created_at=now, updated_at=now,
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
        f"weaved app skeleton: {name} (status=draft, {len(manifest.invocations)} "
        f"invocations, {len(files)} initial files, app.json={'yes' if app_def else 'no'})"
    )
    return meta


def revise_app(
    settings: Settings,
    name: str,
    *,
    description: str | None = None,
    manifest_json: str | None = None,
    app_json: str | None = None,
) -> InvocableAppMeta:
    """改 app 的 manifest.json / app.json / description (整体覆盖).

    用途: 织造期发现 schema 错 / port 冲突 / target 写错时, 改 app.json 不用
    delete + 重 weave. files/ 下源码用 weave_app_write_file / edit_file, 别用本工具.

    限制:
      - app 必须存在
      - status 必须是 draft 或 dirty (ready 的 app 拒, 防止误改已上线 plist 的)
      - 三个字段任一不传 → 保持原值; 至少传一个
      - 跟 weave_app 同款校验 (manifest invocations, app.json schema, target 一致性)

    返更新后的 meta. files/ 不动.
    """
    meta = _check_app_exists(settings, name)
    if meta.status not in ("draft", "dirty"):
        raise index.WeaverError(
            f"revise_app 拒: app {name!r} status={meta.status!r}. "
            f"只允许 draft / dirty 时改 manifest/app.json. "
            f"ready 的 app 想改: 用 weave_app_write_file / weave_app_edit_file 改 "
            f"files/ 下源码 (status 自动打回 dirty), 然后再 revise_app + finalize; "
            f"或 delete 重写."
        )
    if description is None and manifest_json is None and app_json is None:
        raise index.WeaverError(
            "revise_app 需要至少传一个字段 (description / manifest_json / app_json)"
        )

    new_description = description.strip() if description is not None else meta.description
    if not new_description:
        raise index.WeaverError("description 不能为空")

    if manifest_json is not None and manifest_json.strip():
        manifest = parse_manifest(manifest_json)
        if manifest.name != name:
            raise index.WeaverError(
                f"name 跟 manifest.name 不一致: arg={name!r} manifest={manifest.name!r}"
            )
        if manifest.description.strip() != new_description:
            raise index.WeaverError(
                "description 跟 manifest.description 不一致 — 两处应写同一句"
            )
        _validate_manifest_invocations(manifest)
    else:
        manifest = parse_manifest(paths.app_manifest(settings, name).read_text())
        if description is not None and manifest.description.strip() != new_description:
            raise index.WeaverError(
                "改 description 时必须同时传 manifest_json — manifest.description "
                "字段也要更新, 两处保持一致"
            )

    app_def: AppDefinition | None
    if app_json is not None and app_json.strip():
        app_def = parse_app_definition(app_json)
        if app_def.name != name:
            raise index.WeaverError(
                f"name 跟 app.json.name 不一致: arg={name!r} app={app_def.name!r}"
            )
        _validate_app_definition(app_def)
    else:
        app_def = read_app_definition(settings, name)

    _validate_invocation_targets(manifest, app_def)

    if manifest_json is not None:
        paths.app_manifest(settings, name).write_text(
            manifest.model_dump_json(indent=2) + "\n"
        )
    if app_json is not None and app_def is not None:
        paths.app_definition(settings, name).write_text(
            app_def.model_dump_json(indent=2, exclude_none=True) + "\n"
        )
    meta.description = new_description
    _save_meta(settings, meta)

    index.upsert_entry(
        settings,
        IndexEntry(
            name=name, kind="app", description=new_description,
            path=f"apps/{name}/", source=meta.source,
        ),
    )
    logger.info(
        f"revised app: {name} (manifest={'yes' if manifest_json else 'no'}, "
        f"app.json={'yes' if app_json else 'no'}, description={'yes' if description else 'no'})"
    )
    return meta


# ─── 递进式 weave 子工具 ────────────────────────────────────────────────────

_RESERVED_FILES_TOP_LEVEL = frozenset({
    "manifest.json", "app.json", "meta.json",  # 系统维护文件
})


def _check_app_exists(settings: Settings, name: str) -> InvocableAppMeta:
    """write/edit/finalize 入口共用 — 校 app 已在 index + 物理目录 + meta 存在.
    返当前 meta (避免 caller 重复 read)."""
    name = _validate_name(name)
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise index.WeaverError(
            f"app {name!r} 不存在 — 先 weave_app 建骨架, 再 write_file / edit_file"
        )
    if not paths.app_dir(settings, name).exists():
        raise index.WeaverError(
            f"app {name!r} 物理目录已丢 — 用 delete_weaver 清理孤儿条目"
        )
    meta = read_meta(settings, name)
    if meta is None:
        raise index.WeaverError(f"app {name!r} 缺 meta.json (数据损坏)")
    return meta


def _save_meta(settings: Settings, meta: InvocableAppMeta) -> None:
    """落 meta.json + 更新 updated_at."""
    meta.updated_at = datetime.utcnow()
    paths.app_meta(settings, meta.name).write_text(meta.model_dump_json(indent=2))


def bump_use_count(settings: Settings, app_name: str) -> None:
    """invoke_app 成功后调 — 递增 use_count + 更新 last_used_at.

    不动 updated_at (语义是"内容修改", invoke 不算内容修改). 不动 status.
    app 不存在时静默返 (不抛, 防 invoke 已成功反而被 meta 写盘问题搅黄).
    """
    meta = read_meta(settings, app_name)
    if meta is None:
        return
    meta.use_count += 1
    meta.last_used_at = datetime.utcnow()
    # 直接写, 不走 _save_meta 防误更 updated_at
    paths.app_meta(settings, app_name).write_text(meta.model_dump_json(indent=2))


def _demote_to_dirty_if_ready(meta: InvocableAppMeta) -> None:
    """ready app 被改后打回 dirty, 强制重 finalize 以避免 schema 与代码不一致."""
    if meta.status == "ready":
        meta.status = "dirty"
        meta.last_finalize_error = None  # 旧错误已不相关


def _validate_write_target(
    settings: Settings, name: str, rel_path: str, *, label: str
) -> Path:
    """write/edit 共用 — rel_path 校验 + reserved 拒 + resolve 防穿越."""
    safe = _validate_relative_path(rel_path, label=label)
    top = safe.split("/", 1)[0]
    if top in _RESERVED_FILES_TOP_LEVEL:
        raise index.WeaverError(
            f"不能覆盖系统维护文件 {top!r} (改 manifest/app.json/meta 用 edit_weaver)"
        )
    if top in {"runs", "logs"}:
        raise index.WeaverError(f"不能写入 runs/ 或 logs/ (系统目录): {safe}")
    files_root = paths.app_files_dir(settings, name)
    files_root.mkdir(parents=True, exist_ok=True)
    return _resolve_within_files(files_root, safe, label=label)


def write_app_file(
    settings: Settings, app_name: str, rel_path: str, content: str
) -> dict[str, Any]:
    """写 files/<rel_path>. 增量 weave 主力, 自动放行 (app 已 HITL 过)."""
    meta = _check_app_exists(settings, app_name)
    target = _validate_write_target(settings, meta.name, rel_path, label="file")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content)
    _demote_to_dirty_if_ready(meta)
    _save_meta(settings, meta)
    logger.info(
        f"wrote app file: {meta.name}/{rel_path} "
        f"({'replaced' if existed else 'created'}, {len(content)} chars, "
        f"status={meta.status})"
    )
    return {
        "name": meta.name,
        "rel_path": rel_path,
        "bytes": len(content.encode("utf-8")),
        "action": "replaced" if existed else "created",
        "status": meta.status,
    }


def edit_app_file(
    settings: Settings, app_name: str, rel_path: str,
    old_string: str, new_string: str,
) -> dict[str, Any]:
    """改 files/<rel_path> 单段. old_string 必须唯一存在.

    替换 0 次抛 (找不到), >1 次也抛 (歧义, agent 给更长 context 重试).
    """
    meta = _check_app_exists(settings, app_name)
    target = _validate_write_target(settings, meta.name, rel_path, label="file")
    if not target.exists():
        raise index.WeaverError(f"file 不存在: {rel_path} (先 write_file)")
    if old_string == new_string:
        raise index.WeaverError("old_string 跟 new_string 相同, 没意义")
    if not old_string:
        raise index.WeaverError("old_string 不能为空")
    text = target.read_text()
    count = text.count(old_string)
    if count == 0:
        raise index.WeaverError(
            f"old_string 在 {rel_path} 里没找到 (检查空格 / 缩进 / 换行)"
        )
    if count > 1:
        raise index.WeaverError(
            f"old_string 在 {rel_path} 里出现 {count} 次 — 给更长 context 让它唯一"
        )
    new_text = text.replace(old_string, new_string, 1)
    target.write_text(new_text)
    _demote_to_dirty_if_ready(meta)
    _save_meta(settings, meta)
    logger.info(
        f"edited app file: {meta.name}/{rel_path} "
        f"(-{len(old_string)} +{len(new_string)} chars, status={meta.status})"
    )
    return {
        "name": meta.name,
        "rel_path": rel_path,
        "old_chars": len(old_string),
        "new_chars": len(new_string),
        "status": meta.status,
    }


def finalize_app(settings: Settings, app_name: str) -> dict[str, Any]:
    """递进式 weave 的收口点. 校验通过 → status=ready.

    校验:
      1. manifest.json 重新 parse + schema 通过
      2. app.json 重新 parse + schema 通过
      3. target → component 全部存在 (跟 weave 时同款)
      4. 每个 script invocation 的 command 入口文件存在
         (e.g., ["python", "scripts/foo.py"] 检查 files/scripts/foo.py)

    失败: status=failed + last_finalize_error 存原因, 抛 WeaverError 让 agent 看错.
    """
    meta = _check_app_exists(settings, app_name)
    errors: list[str] = []

    # 1. manifest
    try:
        manifest = read_manifest(settings, meta.name)
        _validate_manifest_invocations(manifest)
    except index.WeaverError as e:
        errors.append(f"manifest: {e}")
        manifest = None

    # 2. app.json 必填
    app_def = read_app_definition(settings, meta.name)
    if app_def is None:
        errors.append("缺 app.json (invoke_app 必须有 runtime declaration)")

    # 3. target → component
    if manifest is not None and app_def is not None:
        try:
            _validate_invocation_targets(manifest, app_def)
        except index.WeaverError as e:
            errors.append(f"invocation target: {e}")

    # 4. script command 入口文件存在
    if app_def is not None:
        files_root = paths.app_files_dir(settings, meta.name)
        for script in app_def.components.scripts:
            entry_file = _script_entry_file(script.command, script.workdir)
            if entry_file is None:
                continue  # 非 path-like command (e.g., ["node"]), 跳过这条校验
            wd = files_root
            if script.workdir:
                try:
                    wd = _resolve_within_files(
                        files_root, script.workdir, label=f"script.{script.name}.workdir"
                    )
                except index.WeaverError as e:
                    errors.append(f"script.{script.name}.workdir: {e}")
                    continue
            target = (wd / entry_file).resolve()
            if not target.exists():
                errors.append(
                    f"script.{script.name}.command 入口文件不存在: "
                    f"{entry_file} (期望路径: {target.relative_to(files_root.resolve()) if target.is_relative_to(files_root.resolve()) else target})"
                )

    # 5. dogfood 兜底: window 不依赖 PentaLoom 后端 (8090) / fetch-mode 下 sibling
    # service 必须固定 port / Python service 入口必须含 server 启动调用. 三项防止
    # agent 织出"看起来对但 invoke 起来 exit=0 / 关 PentaLoom 就废"的 app.
    if app_def is not None:
        files_root = paths.app_files_dir(settings, meta.name)
        errors.extend(_validate_window_no_pentaloom_backend(files_root, app_def))
        errors.extend(_validate_service_port_for_fetch_window(files_root, app_def))
        errors.extend(_validate_python_service_entry(files_root, app_def))

    if errors:
        msg = "; ".join(errors)
        meta.status = "failed"
        meta.last_finalize_error = msg[:500]
        _save_meta(settings, meta)
        # finalize 失败 → 清旧 trigger, 避免继续运行不一致版本.
        # stop 失败吞 (旧 plist 可能压根没装过), warning 但不抛.
        try:
            _schedule_trigger_action("stop", settings, meta.name)
        except Exception as cleanup_err:  # noqa: BLE001
            logger.warning(f"finalize cleanup stop failed for {meta.name}: {cleanup_err}")
        logger.warning(f"finalize FAILED: {meta.name} → {msg}")
        raise index.WeaverError(f"finalize 校验失败: {msg}")

    # 校验通过, **先重载 plist** 再 commit ready — plist 写不了就不算真 finalize.
    # reload 失败直接 fail finalize, 避免 status=ready 但 launchd 未安装.
    try:
        _schedule_trigger_action("reload", settings, meta.name)
    except Exception as e:  # noqa: BLE001
        msg = f"plist reload failed: {e}"
        meta.status = "failed"
        meta.last_finalize_error = msg[:500]
        _save_meta(settings, meta)
        logger.warning(f"finalize FAILED (plist): {meta.name} → {msg}")
        raise index.WeaverError(msg) from e

    # plist 都装好了再 commit
    now = datetime.utcnow()
    meta.status = "ready"
    meta.last_finalized_at = now
    meta.last_finalize_error = None
    _save_meta(settings, meta)
    logger.info(f"finalized app: {meta.name} → status=ready")
    return {
        "name": meta.name,
        "status": "ready",
        "finalized_at": now.isoformat() + "Z",
    }


def _script_entry_file(command: list[str], workdir: str | None) -> str | None:
    """从 script.command 启发式找入口文件相对路径.

    `["python", "scripts/h.py"]` → "scripts/h.py"
    `["python", "-m", "pkg"]`     → None (module 入口, 不验)
    `["node", "h.js"]`            → "h.js"
    `["./bin/run"]`               → "bin/run"
    `["custom-cli"]`              → None (PATH 上的 cli, 跳过)

    保守: 只在能明确识别 path-like 时返, 其他返 None 跳过校验.
    """
    if not command:
        return None
    # case 1: interpreter + script (python/node/ruby/etc 都吃第二个 arg 为脚本路径)
    if len(command) >= 2 and command[0] in {"python", "python3", "node", "ruby", "bash", "sh"}:
        # 但要排除 -m / -c 这种 flag 模式
        if command[1].startswith("-"):
            return None
        return command[1]
    # case 2: 单 arg 但明显是相对路径 (含 / 或 .py/.js/.sh 后缀)
    arg0 = command[0]
    if "/" in arg0 or arg0.endswith((".py", ".js", ".sh", ".rb")):
        return arg0.lstrip("./")
    return None


# ─── finalize 兜底校验 helpers ──────────────────────────────────────────────
# 字面量扫 — 单机本地, 不上 AST. 生产代码不会用 obfuscation, 漏识别落到 false
# negative (放过), 不会 false positive 误伤合法代码.

_WINDOW_SOURCE_GLOBS = ("*.ts", "*.tsx", "*.js", "*.jsx")
_PENTALOOM_BACKEND_MARKERS = ("127.0.0.1:8090", "localhost:8090", "/weaver/apps/")
_FETCH_MARKERS = ("fetch(", "axios.", "XMLHttpRequest")
# Python web server 长期运行入口 — Python service 不命中这三个之一, 跑起来立刻
# exit=0 (FastAPI 只定义 app 不启 server 的经典坑).
_PYTHON_SERVE_CALLS = ("uvicorn.run(", "app.run(", "web.run_app(")


def python_entry_arg(command: list[str]) -> str | None:
    """python / python3 命令里抽脚本入口 (跳 -u / -O 等无值 flag).

    支持: ["python", "x.py"] / ["python", "-u", "x.py"] / ["python", "-O", "-u", "x.py"]
    返 None: -m / -c 模式 (module / inline-code, 没文件入口); 找不到 .py 结尾的非 option 参数.

    `-X foo` 这种带值 flag 不识别 (会错把 foo 当候选; 用户 app 不写这种, 接受漏识别).
    """
    if len(command) < 2 or command[0] not in {"python", "python3"}:
        return None
    for arg in command[1:]:
        if arg in {"-m", "-c"}:
            return None  # module / inline-code, 没 .py 文件可校
        if arg.startswith("-"):
            continue  # -u / -O / -OO / -S / -B / -q 等无值 flag
        return arg if arg.endswith(".py") else None
    return None


def _iter_window_sources(files_root: Path) -> list[Path]:
    """files/windows/ 下所有 .ts/.tsx/.js/.jsx. files_root 不存在返空."""
    win_root = files_root / "windows"
    if not win_root.is_dir():
        return []
    out: list[Path] = []
    for pat in _WINDOW_SOURCE_GLOBS:
        out.extend(p for p in win_root.rglob(pat) if p.is_file())
    return sorted(out)


def _scan_lines_for_markers(
    path: Path, markers: tuple[str, ...]
) -> list[tuple[int, str]]:
    """返 (line_no, matched_marker) 列表. 读不了或空文件返空."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in markers:
            if m in line:
                hits.append((lineno, m))
    return hits


def _validate_window_no_pentaloom_backend(
    files_root: Path, app_def: AppDefinition
) -> list[str]:
    """禁止 window 源码出现 127.0.0.1:8090 / localhost:8090 / /weaver/apps/ 字面量.

    出了说明 agent 走了"window fetch PentaLoom 后端转发"这条死路 — 关 PentaLoom
    主壳 window 立刻 Load failed, 违反 invocable app "脱钩主壳"承诺.
    """
    if not app_def.components.windows:
        return []
    out: list[str] = []
    for src in _iter_window_sources(files_root):
        rel = src.relative_to(files_root)
        for lineno, marker in _scan_lines_for_markers(src, _PENTALOOM_BACKEND_MARKERS):
            out.append(
                f"window 源码 {rel}:{lineno} 出现 {marker!r} — 禁止依赖 PentaLoom "
                f"backend (port 8090). 走 fetch service 自己的固定端口, 或用 push "
                f"pattern (agent invoke_app target=window 推数据)."
            )
    return out


def _validate_service_port_for_fetch_window(
    files_root: Path, app_def: AppDefinition
) -> list[str]:
    """window 源码含 fetch(...) → 所有 sibling service 必须固定 port.

    短期取 heuristic: 任一 window 文件命中 _FETCH_MARKERS 即视为 fetch-mode.
    push pattern (window 不主动 fetch, agent 推数据进来) 不命中, port=null 仍 OK.
    """
    if not app_def.components.windows or not app_def.components.services:
        return []
    has_fetch = False
    for src in _iter_window_sources(files_root):
        if _scan_lines_for_markers(src, _FETCH_MARKERS):
            has_fetch = True
            break
    if not has_fetch:
        return []
    null_ports = [s.name for s in app_def.components.services if s.port is None]
    if not null_ports:
        return []
    return [
        f"window 源码含 fetch 调用 (fetch-mode), 当前要求所有 sibling services "
        f"固定 port (≥9000 推荐). 违规 service: {', '.join(null_ports)} (port=null). "
        f"改 app.json components.services[].port 写成固定字面量, 或如果该 service "
        f"只给 agent invoke 用 (window 不会 fetch 它), 把 window 源码里的 fetch "
        f"调用拆走 / 该 service 拆成另一个 app."
    ]


def _validate_python_service_entry(
    files_root: Path, app_def: AppDefinition
) -> list[str]:
    """Python service 入口必须含 uvicorn.run( / app.run( / web.run_app( 之一.

    裸 if __name__ == "__main__": 不算 — 空块同样 exit=0. 走 python_entry_arg 抽
    脚本入口 (支持 -u / -O 等无值 flag); -m / -c / 非 Python 跳.
    """
    out: list[str] = []
    for svc in app_def.components.services:
        cmd = list(svc.command)
        entry_rel = python_entry_arg(cmd)
        if entry_rel is None:
            continue
        # 解析入口 (复用 workdir + relative_to 防穿越)
        wd = files_root
        if svc.workdir:
            try:
                wd = _resolve_within_files(
                    files_root, svc.workdir, label=f"service.{svc.name}.workdir"
                )
            except index.WeaverError as e:
                out.append(f"service.{svc.name}.workdir: {e}")
                continue
        entry = (wd / entry_rel).resolve()
        try:
            entry.relative_to(files_root.resolve())
        except ValueError:
            out.append(
                f"service.{svc.name}.command 入口 {entry_rel!r} 越出 files/ 根"
            )
            continue
        if not entry.is_file():
            # 入口缺失走另一条路径报错; 这里也加一条防漏
            out.append(
                f"service.{svc.name}.command 入口文件不存在: {entry_rel}"
            )
            continue
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            out.append(f"service.{svc.name} 入口读不了 {entry_rel}: {e}")
            continue
        if any(call in text for call in _PYTHON_SERVE_CALLS):
            continue
        rel = entry.relative_to(files_root.resolve())
        out.append(
            f"service.{svc.name} 入口 {rel} 没找到 server 启动调用 "
            f"(uvicorn.run( / app.run( / web.run_app(). 只有 app=FastAPI() 会让 "
            f"`python {entry_rel}` 立刻 exit=0. FastAPI 加:\n"
            f'  if __name__ == "__main__":\n'
            f"      import os, uvicorn\n"
            f'      uvicorn.run(app, host="127.0.0.1", '
            f'port=int(os.environ["PENTALOOM_APP_PORT"]))'
        )
    return out


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


def delete_app_soft(settings: Settings, name: str) -> Path | None:
    """软删: 整个 apps/<name>/ 搬到 weaver/.trash/.

    若物理目录已不存在 (孤儿条目: index 有但目录被外部清掉了), 只清 index entry,
    返 None — 不抛错. 避免调用方绕过 meta-tool 直接改 index.

    service / schedule / watch 统一走 launchd unload 清理; 失败只记录 warning.
    """
    name = _validate_name(name)
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise index.WeaverError(f"app 不存在: {name}")

    # 一次性 unload + 删 app 所有 plist (service + schedule + watch 三类前缀全扫).
    # delete 阶段 plist 可能已经被外部清掉 / 从来没装过, stop 失败仅 warning 不阻断删除.
    try:
        _schedule_trigger_action("stop", settings, name)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"delete_app_soft cleanup stop failed for {name}: {e}")

    src = paths.app_dir(settings, name)
    if not src.exists():
        # 孤儿条目: 只清 index, 不搬 trash (没东西可搬)
        index.remove_entry(settings, "app", name)
        logger.info(f"deleted orphan app entry (no physical dir): {name}")
        return None

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


# ────────────────────────────────────────────────────────────────────
# window 开关 (agent meta-tool + router 都调)
# ────────────────────────────────────────────────────────────────────


async def open_window_for_app(
    settings: Settings, app_name: str, window_name: str | None = None,
) -> dict[str, Any]:
    """开 app 的某个 window — 走 loom socket spawn loomer 子进程.

    window_name=None → 取 app.json components.windows[0]. 多窗 app 显式传名字.
    返 loom 的 {"window_id": str, "pid": int}.

    Raises:
      index.WeaverError: app 不存在 / 缺 app.json / 没有 window 组件 / window
        名字不在 components.windows / entry 越界 / entry 文件不在 / loom 不在.
    """
    from pentaloom.infra import loom_client

    entry_app = index.find_entry(settings, "app", app_name)
    if entry_app is None:
        raise index.WeaverError(f"app {app_name!r} 不存在")

    app_def = read_app_definition(settings, app_name)
    if app_def is None:
        raise index.WeaverError(f"app {app_name!r} 缺 app.json — 先 weave_app_finalize")
    if not app_def.components.windows:
        raise index.WeaverError(f"app {app_name!r} 没有 window 组件 (components.windows 空)")

    if window_name is None:
        spec = app_def.components.windows[0]
    else:
        spec = next(
            (w for w in app_def.components.windows if w.name == window_name), None,
        )
        if spec is None:
            available = [w.name for w in app_def.components.windows]
            raise index.WeaverError(
                f"window {window_name!r} 不在 {app_name}/app.json (有: {available})"
            )

    files_root = paths.app_files_dir(settings, app_name)
    entry_path = (files_root / spec.entry).resolve()
    try:
        entry_path.relative_to(files_root.resolve())
    except ValueError as e:
        raise index.WeaverError(
            f"window entry {spec.entry!r} 越出 files/ 根"
        ) from e
    if not entry_path.exists():
        raise index.WeaverError(f"window entry 文件不存在: {entry_path}")

    try:
        result = await loom_client.open_window(
            entry_path=str(entry_path),
            title=spec.title or app_name,
            width=spec.width or 0,
            height=spec.height or 0,
            app=app_name,
            window_name=spec.name,
        )
    except loom_client.LoomUnavailable as e:
        raise index.WeaverError(
            f"loom daemon 没起 — 跑 `make loom-install` 装系统级 daemon. 原始错: {e}"
        ) from e
    except loom_client.LoomError as e:
        raise index.WeaverError(f"loom call failed: {e}") from e

    bump_use_count(settings, app_name)
    return result


async def close_window_for_app(
    settings: Settings, app_name: str, window_name: str | None = None,
) -> dict[str, Any]:
    """关 app 的某个 window. window_name=None → 取 components.windows[0].

    返 {"closed": bool, "window_name": str}; 窗本来就没开返 closed=False.
    """
    from pentaloom.infra import loom_client

    app_def = read_app_definition(settings, app_name)
    if app_def is None:
        raise index.WeaverError(f"app {app_name!r} 缺 app.json")
    if not app_def.components.windows:
        raise index.WeaverError(f"app {app_name!r} 没有 window 组件")

    if window_name is None:
        wn = app_def.components.windows[0].name
    else:
        if not any(w.name == window_name for w in app_def.components.windows):
            available = [w.name for w in app_def.components.windows]
            raise index.WeaverError(
                f"window {window_name!r} 不在 {app_name}/app.json (有: {available})"
            )
        wn = window_name

    try:
        await loom_client.call("window.close", {"app": app_name, "window_name": wn})
    except loom_client.LoomCommandFailed as e:
        # loom 端 "no window with..." 不算错 — 用户语义是"窗没了"已经达成
        if "no window" in str(e).lower():
            return {"closed": False, "window_name": wn, "note": "窗本来就没开"}
        raise index.WeaverError(f"loom close failed: {e}") from e
    except loom_client.LoomUnavailable as e:
        raise index.WeaverError(
            f"loom daemon 没起 — 跑 `make loom-install`. 原始错: {e}"
        ) from e
    except loom_client.LoomError as e:
        raise index.WeaverError(f"loom call failed: {e}") from e

    return {"closed": True, "window_name": wn}
