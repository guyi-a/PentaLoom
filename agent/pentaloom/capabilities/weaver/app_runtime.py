"""Invocable App runtime — Phase B (script subprocess) 实施.

invoke_app(app_name, invocation_id, args) → {run_id, status, duration_ms, output}:
  1. 读 manifest + app.json (manifest 必填, app.json 必填 — Phase A 宽松省了的话拒)
  2. 找 invocation_id + target — Phase B 只支持 target.component='script'
  3. JSON Schema 校验 input
  4. spawn script via uv venv subprocess, stdin 喂 {invocation_id, args, run_id} JSON;
     cwd = files/<script.workdir> (workdir 可选, 不写就是 files/ 根)
  5. parse stdout JSON, JSON Schema 校验 output
  6. 写 InvocationRun 日志到 logs/runs.jsonl (单文件 append, 简单 query)
  7. 返 {run_id, status, duration_ms, output} — output 是 handler 实际返的;
     run_id / duration_ms 给 agent 排查日志 + 反馈用户耗时. handler 输出包在
     output 里, 不平铺到 top-level 是为了不跟 run_id 等元字段名字撞.

Phase C (window) / Phase D (service) 留后续, 各自走不同 runtime path.

不在 Phase B:
  - WorkflowRun DB 表 — 不引 DB (跟 weaver M14 一致, 文件 SoT)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
from loguru import logger

from pentaloom.capabilities.weaver import app as app_biz
from pentaloom.capabilities.weaver import index, paths
from pentaloom.capabilities.weaver.models import (
    AppDefinition,
    AppScriptSpec,
    InvocableAppManifest,
    InvocationSpec,
)
from pentaloom.config import Settings
from pentaloom.infra import python_env


class InvokeError(Exception):
    """invoke_app 业务错误. tools/weaver.py 包成 is_error 帧."""


def _find_invocation(
    manifest: InvocableAppManifest, invocation_id: str
) -> InvocationSpec:
    for inv in manifest.invocations:
        if inv.id == invocation_id:
            return inv
    available = [i.id for i in manifest.invocations]
    raise InvokeError(
        f"invocation {invocation_id!r} 不在 manifest. 可用: {available}"
    )


def _find_script_component(
    app_def: AppDefinition, name: str
) -> AppScriptSpec:
    for s in app_def.components.scripts:
        if s.name == name:
            return s
    available = [s.name for s in app_def.components.scripts]
    raise InvokeError(
        f"script component {name!r} 不在 app.json. 可用: {available}"
    )


def _resolve_workdir(files_root: Path, workdir: str | None) -> Path:
    """script.workdir 是 files/ 下的相对路径, 不允许 ../ 或绝对.
    None / '' → 直接用 files/ 根."""
    if not workdir or not workdir.strip():
        return files_root
    wd = workdir.strip()
    if wd.startswith("/") or "\\" in wd:
        raise InvokeError(f"workdir 必须是 files/ 下的相对路径: {workdir!r}")
    target = (files_root / wd).resolve()
    files_resolved = files_root.resolve()
    try:
        target.relative_to(files_resolved)
    except ValueError as e:
        raise InvokeError(
            f"workdir {workdir!r} 越出 files/ 根 (路径穿越)"
        ) from e
    if not target.exists():
        raise InvokeError(f"workdir 不存在: {workdir!r} → {target}")
    if not target.is_dir():
        raise InvokeError(f"workdir 不是目录: {workdir!r}")
    return target


def _validate_input(args: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        jsonschema.validate(args, schema)
    except jsonschema.ValidationError as e:
        raise InvokeError(f"input schema 校验失败: {e.message}") from e


def _validate_output(output: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        jsonschema.validate(output, schema)
    except jsonschema.ValidationError as e:
        raise InvokeError(f"output schema 校验失败: {e.message}") from e


def _new_run_id() -> str:
    """run_id 用 timestamp + 短 uuid — 文件系统排序友好 + 防同毫秒碰撞."""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"


def _append_run_log(
    settings: Settings,
    app_name: str,
    run_id: str,
    invocation_id: str,
    status: str,
    duration_ms: int,
    error: str | None = None,
) -> None:
    """invocation 历史落到 logs/runs.jsonl — 单文件 append-only.

    不引 DB. tail_weaver_logs(kind='app') 反过来读这文件.
    """
    log_file = paths.app_logs_dir(settings, app_name) / "runs.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": run_id,
        "invocation_id": invocation_id,
        "status": status,
        "duration_ms": duration_ms,
        "started_at": datetime.utcnow().isoformat() + "Z",
    }
    if error:
        entry["error"] = error[:500]  # 截 500 字, 防一坨 traceback 撑爆文件
    with log_file.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def invoke_app(
    settings: Settings,
    *,
    app_name: str,
    invocation_id: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Phase B 入口 — 调一个 invocable app 的 script invocation.

    返 {run_id, status, duration_ms, output}. 失败抛 InvokeError (附 run_id 在 message 里).
    """
    args = args or {}
    started_at_ms = int(datetime.utcnow().timestamp() * 1000)
    run_id = _new_run_id()

    # ─── 1. 读 manifest + app.json + meta (Phase B 必须有 app.json + status=ready) ───
    try:
        manifest = app_biz.read_manifest(settings, app_name)
    except index.WeaverError as e:
        raise InvokeError(f"manifest 读失败 (run {run_id}): {e}") from e

    app_def = app_biz.read_app_definition(settings, app_name)
    if app_def is None:
        raise InvokeError(
            f"app {app_name!r} 缺 app.json (Phase B 必填; Phase A 宽松不行了)"
        )

    # 状态机收口 (Fix 1 / GPT): 只允许 status=ready 的 app 被 invoke.
    # draft / dirty / failed 都拒, 让 agent 先 weave_app_finalize.
    meta = app_biz.read_meta(settings, app_name)
    if meta is None:
        raise InvokeError(f"app {app_name!r} 缺 meta.json (数据损坏)")
    if meta.status != "ready":
        hint = {
            "draft": "app 还在写, 写完用 weave_app_finalize 收口",
            "dirty": "app 改过文件没重新 finalize, 用 weave_app_finalize 重校",
            "failed": f"上次 finalize 失败 ({meta.last_finalize_error or 'unknown'}), 修完用 weave_app_finalize 重校",
        }.get(meta.status, "status 不对劲")
        raise InvokeError(
            f"app {app_name!r} status={meta.status!r}, 不能 invoke. {hint}"
        )

    # ─── 2. 找 invocation + target.component='script' ───
    inv = _find_invocation(manifest, invocation_id)
    if inv.target is None:
        raise InvokeError(
            f"invocation {invocation_id!r} 缺 target (Phase B 必填 — Phase A 占位不再宽松)"
        )
    if inv.target.component != "script":
        raise InvokeError(
            f"Phase B 只支持 target.component='script'; 收到 {inv.target.component!r} "
            f"(window=Phase C, service=Phase D)"
        )
    script = _find_script_component(app_def, inv.target.name)

    # ─── 3. JSON Schema 校 input ───
    _validate_input(args, inv.input_schema)

    # ─── 4. spawn ───
    # stdin 协议: {invocation_id, args, run_id} 整包给 script, script 自己 dispatch.
    # 跟 spike_app_manifest_loop 的 Node runner 同款约定. run_id 让 script 自己
    # 写 artifact 时可用 (e.g., runs/<run_id>/output.png).
    stdin_payload = json.dumps(
        {"invocation_id": invocation_id, "args": args, "run_id": run_id},
        ensure_ascii=False,
    ).encode("utf-8")

    timeout_s = max(1, inv.timeout_ms // 1000)
    files_root = paths.app_files_dir(settings, app_name)
    cwd = _resolve_workdir(files_root, script.workdir)
    logger.info(
        f"invoke_app: {app_name}/{invocation_id} run={run_id} "
        f"script={' '.join(script.command)} cwd={cwd} timeout={timeout_s}s"
    )
    result = await python_env.run_app_script(
        settings,
        cwd=cwd,
        command=script.command,
        stdin_data=stdin_payload,
        timeout=timeout_s,
    )

    duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms

    # ─── 5. parse + 校验 output ───
    if result.exit_code != 0:
        err = f"handler exit {result.exit_code}: {result.stderr[:400]}"
        _append_run_log(settings, app_name, run_id, invocation_id, "failed", duration_ms, err)
        raise InvokeError(f"{err} (run {run_id})")

    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        err = f"handler 输出不是合法 JSON: {result.stdout[:200]} (stderr: {result.stderr[:200]})"
        _append_run_log(settings, app_name, run_id, invocation_id, "failed", duration_ms, err)
        raise InvokeError(f"{err} (run {run_id})") from e

    try:
        _validate_output(output, inv.output_schema)
    except InvokeError as e:
        _append_run_log(settings, app_name, run_id, invocation_id, "failed", duration_ms, str(e))
        raise InvokeError(f"{e} (run {run_id})") from e

    # ─── 6. 成功落日志 + 返带元信息 ───
    _append_run_log(settings, app_name, run_id, invocation_id, "success", duration_ms)
    logger.info(f"invoke_app success: {app_name}/{invocation_id} run={run_id} {duration_ms}ms")
    return {
        "run_id": run_id,
        "status": "success",
        "duration_ms": duration_ms,
        "output": output,
    }


def tail_run_logs(
    settings: Settings, app_name: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    """读 logs/runs.jsonl 最近 N 条 — 给 tail_weaver_logs(kind='app') 用."""
    log_file = paths.app_logs_dir(settings, app_name) / "runs.jsonl"
    if not log_file.exists():
        return []
    lines = log_file.read_text().splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
