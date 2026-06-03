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
    """禁 path traversal. POSIX 风格, 相对路径.

    字符串层面拒明显穿越. resolve 后再二次校验 (`_resolve_within_files`) 才是
    最终防御 — 单靠字符串挡不住符号链接 / 大小写差异 / NUL 字节等. GPT 建议.
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

    防 symlink / .. 残留 / 大小写规范化绕过 (GPT 强调).
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
    files: dict[str, str] | None = None,
    *,
    app_json: str | None = None,
    source: WeaverSource = "agent_woven",
) -> InvocableAppMeta:
    """织一个 invocable app 骨架 (递进式 weave 入口, GPT 重设计后版本).

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


# ─── 递进式 weave 子工具 (Fix 1, GPT 设计) ─────────────────────────────────

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
    """ready app 被改了 → 打回 dirty, 强制重 finalize (旧 schema + 新代码不一致风险, GPT)."""
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
    """改 files/<rel_path> 单段. 跟 SDK Edit 同款语义 (old 必须唯一存在).

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
    """递进式 weave 的收口点 (GPT 建议). 4 项校验通过 → status=ready.

    校验:
      1. manifest.json 重新 parse + schema 通过
      2. app.json 重新 parse + schema 通过 (没 app.json 拒 — Phase B+ 必须有)
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

    # 2. app.json (Phase B+ 必填)
    app_def = read_app_definition(settings, meta.name)
    if app_def is None:
        errors.append("缺 app.json (Phase B+ invoke_app 必须有 runtime declaration)")

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

    if errors:
        msg = "; ".join(errors)
        meta.status = "failed"
        meta.last_finalize_error = msg[:500]
        _save_meta(settings, meta)
        logger.warning(f"finalize FAILED: {meta.name} → {msg}")
        raise index.WeaverError(f"finalize 校验失败: {msg}")

    # 通过
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
    返 None — 不抛错. 否则 agent 会尝试 workaround (绕过 meta-tool 直接改 index),
    这是 Fix 6 + Fix 8 一起防的攻击面.
    """
    name = _validate_name(name)
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise index.WeaverError(f"app 不存在: {name}")

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
