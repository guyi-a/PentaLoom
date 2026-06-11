"""Workflow 业务层 — M17 dynamic workflow.

跟 app.py / skill.py 平级, 但比 app 简单 (workflow 没 files/ 子树, 整个就一个
workflow.json 主文件 + meta.json + logs/runs.jsonl).

公开 API:
  - weave_workflow(settings, name, description, definition_json) → WorkflowMeta
  - edit_workflow(settings, name, new_definition_json) → WorkflowMeta (status → dirty)
  - finalize_workflow(settings, name) → {name, status, finalized_at}
  - delete_workflow_soft(settings, name) → trash_path
  - read_workflow / read_meta / list_workflow_summary
  - bump_use_count (workflow_runtime 调)

校验时机:
  - weave_workflow / edit_workflow: schema (含 step.id 唯一 + 格式) + dup name
  - finalize_workflow: 跑 5 项校验 — schema + step.id + mustache 引用 + invoke_app 软校
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from pentaloom.capabilities.weaver import index, paths
from pentaloom.capabilities.weaver.models import (
    IndexEntry,
    WeaverSource,
    WorkflowDefinition,
    WorkflowMeta,
)
from pentaloom.config import Settings


# ─── Mustache 引用解析 (finalize 校验用) ─────────────────────────────────────
# 简单 path lookup, 不引第三方库. runtime 共用同一规则 (workflow_runtime.py).

_MUSTACHE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _extract_refs(value: Any) -> list[str]:
    """递归找 dict/list/str 里所有 {{...}} 引用 path."""
    refs: list[str] = []
    if isinstance(value, str):
        refs.extend(_MUSTACHE_RE.findall(value))
    elif isinstance(value, dict):
        for v in value.values():
            refs.extend(_extract_refs(v))
    elif isinstance(value, list):
        for v in value:
            refs.extend(_extract_refs(v))
    return refs


# ─── 名字校验 (跟 app._validate_name 对齐, 简化版) ──────────────────────────

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise index.WeaverError(
            f"workflow name 不合法 (kebab-case, 1-64 字符, 字母数字横线): {name!r}"
        )
    return name


# ─── 写盘 ───────────────────────────────────────────────────────────────────

def parse_definition(text: str) -> WorkflowDefinition:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise index.WeaverError(f"workflow.json 不是合法 JSON: {e}") from e
    try:
        return WorkflowDefinition.model_validate(data)
    except Exception as e:
        raise index.WeaverError(f"workflow.json schema 不通过: {e}") from e


def weave_workflow(
    settings: Settings,
    *,
    name: str,
    description: str,
    definition_json: str,
    source: WeaverSource = "agent_woven",
) -> WorkflowMeta:
    """建一个新 workflow (status=draft). agent 调 weave_workflow tool 落到这。"""
    name = _validate_name(name)
    description = (description or "").strip()
    if not description:
        raise index.WeaverError("description 不能为空")

    definition = parse_definition(definition_json)
    if definition.name != name:
        raise index.WeaverError(
            f"name 跟 workflow.json.name 不一致: tool={name!r} json={definition.name!r}"
        )
    if definition.description.strip() != description:
        raise index.WeaverError(
            "description 跟 workflow.json.description 不一致 — 两处应写同一句"
        )

    # 跨 kind 不重名 (含内置 skill)
    occupied = index.name_exists_any_kind(settings, name)
    if occupied is not None:
        raise index.WeaverError(
            f"名字已被占用 (kind={occupied}, name={name!r}). 用 edit_weaver 改, "
            "或者换个名字"
        )
    from pentaloom.prompts.skills import ENABLED_SKILLS
    if name in ENABLED_SKILLS:
        raise index.WeaverError(
            f"名字 {name!r} 跟内置 skill 冲突, 换个名字"
        )

    # 写盘 — 整个 workflow 就 workflow.json + meta.json + (空) logs/runs/
    paths.ensure_dirs(settings)
    workflow_dir = paths.workflow_dir(settings, name)
    workflow_dir.mkdir(parents=True, exist_ok=False)
    paths.ensure_workflow_dirs(settings, name)

    paths.workflow_json(settings, name).write_text(
        definition.model_dump_json(indent=2), encoding="utf-8"
    )

    meta = WorkflowMeta(
        name=name,
        description=description,
        source=source,
        status="draft",
    )
    paths.workflow_meta(settings, name).write_text(
        meta.model_dump_json(indent=2), encoding="utf-8"
    )

    index.upsert_entry(
        settings,
        IndexEntry(
            name=name, kind="workflow", description=description,
            path=f"workflows/{name}/", source=source,
        ),
    )
    logger.info(f"weave_workflow: {name} → status=draft ({len(definition.steps)} steps)")
    return meta


def edit_workflow(
    settings: Settings,
    *,
    name: str,
    new_definition_json: str,
) -> WorkflowMeta:
    """改 workflow.json — name 不许变, ready → dirty 强制重 finalize."""
    meta = _check_workflow_exists(settings, name)
    new_def = parse_definition(new_definition_json)
    if new_def.name != meta.name:
        raise index.WeaverError(
            f"workflow.json.name 不能改 (旧={meta.name!r} 新={new_def.name!r})"
        )

    paths.workflow_json(settings, name).write_text(
        new_def.model_dump_json(indent=2), encoding="utf-8"
    )

    # description 同步到 meta + index
    if new_def.description.strip() != meta.description:
        meta.description = new_def.description.strip()
        # 刷 index entry description, 跟 app 一致
        idx = index.load_index(settings)
        for e in idx.workflows:
            if e.name == name:
                e.description = meta.description
                break
        index.save_index(settings, idx)

    _demote_to_dirty_if_ready(meta)
    _save_meta(settings, meta)
    logger.info(f"edit_workflow: {name} → status={meta.status}")
    return meta


def finalize_workflow(settings: Settings, name: str) -> dict[str, Any]:
    """跑校验, 通过 → status=ready, 失败 → status=failed + raise.

    返 {name, status, finalized_at, warnings?} — warnings 是软校发现的潜在问题
    (e.g., invoke_app 引用的 app/invocation 现在不存在 — 不挡 finalize 但 runtime
    会报错). agent 看到 warnings 应该决定是补织对应 app 还是改 step.
    """
    meta = _check_workflow_exists(settings, name)
    errors: list[str] = []
    warnings: list[str] = []

    try:
        definition = read_workflow(settings, name)
    except index.WeaverError as e:
        errors.append(f"workflow.json: {e}")
        definition = None

    if definition is not None:
        # 1. step.id 唯一 + 格式 — schema 已校, 这里不重复
        # 2. mustache 引用合法 (forward-ref + inputs key 存在 + 用什么字段)
        seen_ids: list[str] = []
        for step in definition.steps:
            # 收集这个 step 引用的 path
            step_value: Any
            if step.kind == "invoke_app":
                step_value = step.args
            elif step.kind == "call_llm":
                step_value = {"prompt": step.prompt, "system": step.system}
            else:  # set_var
                step_value = step.value

            for ref in _extract_refs(step_value):
                head, *rest = ref.split(".")
                if head == "inputs":
                    if not rest:
                        errors.append(
                            f"step {step.id!r} 引用 {{{{{ref}}}}} 不合法 — inputs 后必须跟 key"
                        )
                        continue
                    key = rest[0]
                    props = (definition.inputs_schema or {}).get("properties", {})
                    if key not in props:
                        errors.append(
                            f"step {step.id!r} 引用 inputs.{key} 不在 inputs_schema.properties"
                        )
                elif head == "steps":
                    if len(rest) < 2:
                        errors.append(
                            f"step {step.id!r} 引用 {{{{{ref}}}}} 不合法 — steps.<id>.<field> 至少 3 段"
                        )
                        continue
                    sid, field = rest[0], rest[1]
                    if sid not in seen_ids:
                        errors.append(
                            f"step {step.id!r} 引用 steps.{sid} 在 self 之前没出现 (forward-ref)"
                        )
                    if field != "output" and field != "value":
                        errors.append(
                            f"step {step.id!r} 引用 steps.{sid}.{field} 不合法 (只支持 .output 或 set_var 的 .value)"
                        )
                else:
                    errors.append(
                        f"step {step.id!r} 引用 {{{{{ref}}}}} 不合法 — 根必须是 inputs 或 steps"
                    )
            seen_ids.append(step.id)

        # 3. output_template 也校引用 (可空) — 跟 step 内引用同一套规则
        if definition.output_template is not None:
            for ref in _extract_refs(definition.output_template):
                head, *rest = ref.split(".")
                if head == "inputs":
                    if not rest:
                        errors.append(
                            f"output_template 引用 {{{{{ref}}}}} 不合法 — inputs 后必须跟 key"
                        )
                        continue
                    key = rest[0]
                    props = (definition.inputs_schema or {}).get("properties", {})
                    if key not in props:
                        errors.append(
                            f"output_template 引用 inputs.{key} 不在 inputs_schema.properties"
                        )
                elif head == "steps":
                    if len(rest) < 1:
                        errors.append(
                            f"output_template 引用 {{{{{ref}}}}} 不合法 — steps.<id> 至少 2 段"
                        )
                        continue
                    if rest[0] not in seen_ids:
                        errors.append(
                            f"output_template 引用 steps.{rest[0]} 不存在"
                        )
                else:
                    errors.append(
                        f"output_template 引用 {{{{{ref}}}}} 不合法 — 根必须是 inputs 或 steps"
                    )

        # 4. 软校 invoke_app 的 app_name + invocation_id 在 manifest.invocations 里
        # invocation_id 错是常见手贱坑 (agent 容易默认填 app_name 当 invocation_id,
        # 但 manifest 里 invocation_id 通常是动词如 'count' / 'summarize').
        # 软校发 warning 不挡 finalize (容忍"先织 workflow 后织 app"), 但回流给 agent.
        for step in definition.steps:
            if step.kind != "invoke_app":
                continue
            entry = index.find_entry(settings, "app", step.app_name)
            if entry is None:
                warnings.append(
                    f"step {step.id!r}: app {step.app_name!r} 不存在 — "
                    "可能 workflow 先织 app 后织, runtime 调时才报错"
                )
                continue
            # app 存在, 接着校 invocation_id 是否在 manifest.invocations 里
            try:
                from pentaloom.capabilities.weaver import app as app_biz
                manifest = app_biz.read_manifest(settings, step.app_name)
            except index.WeaverError:
                # manifest 读不出来; 不刷过多警告, 跳过
                continue
            invocation_ids = [inv.id for inv in manifest.invocations]
            if step.invocation_id not in invocation_ids:
                warnings.append(
                    f"step {step.id!r}: {step.app_name}/{step.invocation_id} 不存在 "
                    f"(可用 invocation: {invocation_ids}) — 跑时会失败, 建议改"
                )

    if errors:
        msg = "; ".join(errors)
        meta.status = "failed"
        meta.last_finalize_error = msg[:500]
        _save_meta(settings, meta)
        logger.warning(f"finalize_workflow FAILED: {name} → {msg}")
        raise index.WeaverError(f"finalize 校验失败: {msg}")

    now = datetime.utcnow()
    meta.status = "ready"
    meta.last_finalized_at = now
    meta.last_finalize_error = None
    _save_meta(settings, meta)
    if warnings:
        logger.warning(
            f"finalize_workflow: {name} → status=ready (with {len(warnings)} warnings)"
        )
    else:
        logger.info(f"finalize_workflow: {name} → status=ready")
    result: dict[str, Any] = {
        "name": name,
        "status": "ready",
        "finalized_at": now.isoformat() + "Z",
    }
    if warnings:
        result["warnings"] = warnings
    return result


def delete_workflow_soft(settings: Settings, name: str) -> Path | None:
    """整个 workflow_dir 搬到 .trash/, 清 index. 跟 app.delete_app_soft 同款."""
    name = _validate_name(name)
    entry = index.find_entry(settings, "workflow", name)
    if entry is None:
        raise index.WeaverError(f"workflow 不存在: {name}")

    src = paths.workflow_dir(settings, name)
    if not src.exists():
        index.remove_entry(settings, "workflow", name)
        logger.info(f"deleted orphan workflow entry (no physical dir): {name}")
        return None

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    trash_target = paths.trash_dir(settings) / f"workflow-{name}-{ts}"
    trash_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(trash_target))
    index.remove_entry(settings, "workflow", name)
    logger.info(f"deleted workflow {name} → trash {trash_target.name}")
    return trash_target


# ─── Read ──────────────────────────────────────────────────────────────────

def read_workflow(settings: Settings, name: str) -> WorkflowDefinition:
    p = paths.workflow_json(settings, name)
    if not p.exists():
        raise index.WeaverError(f"workflow.json 不存在: {p}")
    return parse_definition(p.read_text(encoding="utf-8"))


def read_meta(settings: Settings, name: str) -> WorkflowMeta | None:
    p = paths.workflow_meta(settings, name)
    if not p.exists():
        return None
    return WorkflowMeta.model_validate_json(p.read_text(encoding="utf-8"))


def list_workflow_summary(settings: Settings, name: str) -> dict[str, Any]:
    """给 inspect_weaver(kind=workflow) 用 — 返摘要 (含 definition + meta)."""
    definition = read_workflow(settings, name)
    meta = read_meta(settings, name)
    return {
        "name": name,
        "description": definition.description,
        "version": definition.version,
        "step_count": len(definition.steps),
        "steps_summary": [
            {"id": s.id, "kind": s.kind} for s in definition.steps
        ],
        "definition": definition.model_dump(mode="json"),
        "meta": meta.model_dump(mode="json") if meta else None,
    }


# ─── 内部 helpers ──────────────────────────────────────────────────────────

def _check_workflow_exists(settings: Settings, name: str) -> WorkflowMeta:
    name = _validate_name(name)
    entry = index.find_entry(settings, "workflow", name)
    if entry is None:
        raise index.WeaverError(f"workflow {name!r} 不存在 — 先 weave_workflow 建")
    if not paths.workflow_dir(settings, name).exists():
        raise index.WeaverError(
            f"workflow {name!r} 物理目录已丢 — 用 delete_weaver 清理孤儿"
        )
    meta = read_meta(settings, name)
    if meta is None:
        raise index.WeaverError(f"workflow {name!r} 缺 meta.json (数据损坏)")
    return meta


def _save_meta(settings: Settings, meta: WorkflowMeta) -> None:
    meta.updated_at = datetime.utcnow()
    paths.workflow_meta(settings, meta.name).write_text(
        meta.model_dump_json(indent=2), encoding="utf-8"
    )


def bump_use_count(settings: Settings, name: str) -> None:
    """invoke_workflow 成功后调. 只动 use_count + last_used_at, 不动 updated_at."""
    meta = read_meta(settings, name)
    if meta is None:
        return
    meta.use_count += 1
    meta.last_used_at = datetime.utcnow()
    paths.workflow_meta(settings, name).write_text(
        meta.model_dump_json(indent=2), encoding="utf-8"
    )


def _demote_to_dirty_if_ready(meta: WorkflowMeta) -> None:
    if meta.status == "ready":
        meta.status = "dirty"
        meta.last_finalize_error = None
