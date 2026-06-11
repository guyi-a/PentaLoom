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

import asyncio
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
    trigger: str = "user",
) -> None:
    """invocation 历史落到 logs/runs.jsonl — 单文件 append-only.

    status: success / failed / skipped (M16 Phase E 加 skipped — schedule overlap /
    watch in-flight / status race).
    trigger: user (default, MCP tool / window WS 调用) / schedule / watch / workflow
    (M17, workflow_runtime.invoke_workflow 调用 invoke_app step 时透传).

    不引 DB. tail_weaver_logs(kind='app') 反过来读这文件; 旧 entry 缺 trigger 字段
    读取时默认 user 兼容.
    """
    log_file = paths.app_logs_dir(settings, app_name) / "runs.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": run_id,
        "invocation_id": invocation_id,
        "status": status,
        "duration_ms": duration_ms,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "trigger": trigger,
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
    trigger: str = "user",
) -> dict[str, Any]:
    """Phase B 入口 — 调一个 invocable app 的 script invocation.

    trigger: user (MCP tool / window WS, default) / schedule (cron 触发) /
    watch (fs event 触发). 透传到 _invoke_*, runs.jsonl 落地以区分来源.

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

    # ─── 2. 找 invocation + target dispatch (script / window) ───
    inv = _find_invocation(manifest, invocation_id)
    if inv.target is None:
        raise InvokeError(
            f"invocation {invocation_id!r} 缺 target (Phase B 必填 — Phase A 占位不再宽松)"
        )
    target_kind = inv.target.component
    if target_kind == "script":
        return await _invoke_script(
            settings, app_name, app_def, inv, args, run_id, started_at_ms, trigger,
        )
    if target_kind == "window":
        return await _invoke_window(
            settings, app_name, inv, args, run_id, started_at_ms, trigger,
        )
    if target_kind == "service":
        return await _invoke_service(
            settings, app_name, inv, args, run_id, started_at_ms, trigger,
        )
    raise InvokeError(
        f"target.component 不合法 (script | window | service): 收到 {target_kind!r}"
    )


async def _invoke_window(
    settings: Settings,
    app_name: str,
    inv: InvocationSpec,
    args: dict[str, Any],
    run_id: str,
    started_at_ms: int,
    trigger: str = "user",
) -> dict[str, Any]:
    """target.component='window' 路径 — 走 WebSocket 推到 window registered handler.

    跟 _invoke_script 平级. registry 找不到 ws → 拒"window not open"; 找得到 →
    推 invoke 消息 + 等 result Future (with timeout) → 校 output_schema → return.
    """
    from pentaloom.capabilities.weaver.window_registry import window_registry

    _validate_input(args, inv.input_schema)
    reg = window_registry()
    ws = reg.get_one(app_name)
    if ws is None:
        raise InvokeError(
            f"app {app_name!r} 的 window 没打开 — agent 无法调 window invocation. "
            f"让用户在 sidebar 点 Open 先开窗, 或后续 Phase 加 auto-open"
        )

    request_id, fut = reg.new_request()
    timeout_s = max(1, inv.timeout_ms // 1000)
    logger.info(
        f"invoke_app(window): {app_name}/{inv.id} run={run_id} request={request_id} "
        f"timeout={timeout_s}s"
    )

    try:
        await ws.send_json({
            "type": "invoke",
            "request_id": request_id,
            "invocation_id": inv.id,
            "args": args,
        })
    except Exception as e:
        reg.resolve(request_id, {})  # 清 pending
        raise InvokeError(f"window ws send 失败 (run {run_id}): {e}") from e

    try:
        msg = await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError as e:
        raise InvokeError(
            f"window invocation {inv.id!r} 超时 ({timeout_s}s, run {run_id})"
        ) from e

    duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms

    if msg.get("type") == "invoke_error":
        err = str(msg.get("error", "unknown window error"))
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})")

    output = msg.get("output") if isinstance(msg.get("output"), dict) else {}
    try:
        _validate_output(output, inv.output_schema)
    except InvokeError as e:
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, str(e), trigger=trigger)
        raise InvokeError(f"{e} (run {run_id})") from e

    _append_run_log(settings, app_name, run_id, inv.id, "success", duration_ms, trigger=trigger)
    # window invocation 也算 use, 跟 script 同款递增
    app_biz.bump_use_count(settings, app_name)
    logger.info(f"invoke_app(window) success: {app_name}/{inv.id} run={run_id} {duration_ms}ms")
    return {
        "run_id": run_id,
        "status": "success",
        "duration_ms": duration_ms,
        "output": output,
    }


async def _invoke_service(
    settings: Settings,
    app_name: str,
    inv: InvocationSpec,
    args: dict[str, Any],
    run_id: str,
    started_at_ms: int,
    trigger: str = "user",
) -> dict[str, Any]:
    """target.component='service' 路径 — lazy spawn service + HTTP fetch.

    跟 _invoke_script / _invoke_window 平级. service 没起 → ensure_service 起;
    http 调失败 → InvokeError; 成功 → schema 校 → return.
    """
    import httpx

    from pentaloom.capabilities.weaver.service_registry import (
        ServiceError, service_registry,
    )

    _validate_input(args, inv.input_schema)

    # target 必须含 method + path (path validator 已校 / 开头 + 拒 ://)
    method = (inv.target.method or "POST").upper()
    path = inv.target.path or "/"
    if method not in ("GET", "POST"):
        raise InvokeError(f"target.method 只支持 GET/POST, 收到 {method!r}")

    reg = service_registry()
    try:
        rs = await reg.ensure_service(settings, app_name, inv.target.name)
    except ServiceError as e:
        raise InvokeError(f"service 起不来 (run {run_id}): {e}") from e

    timeout_s = max(1, inv.timeout_ms // 1000)
    url = f"{rs.base_url}{path}"
    logger.info(
        f"invoke_app(service): {app_name}/{inv.id} run={run_id} {method} {url} timeout={timeout_s}s"
    )

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            if method == "GET":
                # GET 把 args 当 query (扁平 string 化)
                params = {k: str(v) for k, v in args.items()}
                http_resp = await client.get(url, params=params)
            else:
                http_resp = await client.post(url, json=args)
    except httpx.TimeoutException as e:
        duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms
        err = f"service 超时 ({timeout_s}s): {e}"
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})") from e
    except httpx.HTTPError as e:
        duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms
        err = f"service 连接失败: {e}"
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})") from e

    duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms

    if http_resp.status_code >= 400:
        err = f"service {method} {path} → HTTP {http_resp.status_code}: {http_resp.text[:300]}"
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})")

    try:
        output = http_resp.json()
        if not isinstance(output, dict):
            raise ValueError(f"top-level 不是 object, 是 {type(output).__name__}")
    except (ValueError, Exception) as e:
        err = f"service response 不是合法 JSON object: {http_resp.text[:200]}"
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})") from e

    try:
        _validate_output(output, inv.output_schema)
    except InvokeError as e:
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, str(e), trigger=trigger)
        raise InvokeError(f"{e} (run {run_id})") from e

    _append_run_log(settings, app_name, run_id, inv.id, "success", duration_ms, trigger=trigger)
    app_biz.bump_use_count(settings, app_name)
    logger.info(f"invoke_app(service) success: {app_name}/{inv.id} run={run_id} {duration_ms}ms")
    return {
        "run_id": run_id,
        "status": "success",
        "duration_ms": duration_ms,
        "output": output,
    }


async def _invoke_script(
    settings: Settings,
    app_name: str,
    app_def: AppDefinition,
    inv: InvocationSpec,
    args: dict[str, Any],
    run_id: str,
    started_at_ms: int,
    trigger: str = "user",
) -> dict[str, Any]:
    """target.component='script' 路径 — uv venv subprocess + stdin/stdout JSON.

    原 invoke_app 的主体, 抽出来跟 _invoke_window 平级.
    """
    script = _find_script_component(app_def, inv.target.name)
    _validate_input(args, inv.input_schema)

    stdin_payload = json.dumps(
        {"invocation_id": inv.id, "args": args, "run_id": run_id},
        ensure_ascii=False,
    ).encode("utf-8")

    timeout_s = max(1, inv.timeout_ms // 1000)
    files_root = paths.app_files_dir(settings, app_name)
    cwd = _resolve_workdir(files_root, script.workdir)
    logger.info(
        f"invoke_app(script): {app_name}/{inv.id} run={run_id} "
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

    if result.exit_code != 0:
        err = f"handler exit {result.exit_code}: {result.stderr[:400]}"
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})")

    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        err = f"handler 输出不是合法 JSON: {result.stdout[:200]} (stderr: {result.stderr[:200]})"
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})") from e

    try:
        _validate_output(output, inv.output_schema)
    except InvokeError as e:
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, str(e), trigger=trigger)
        raise InvokeError(f"{e} (run {run_id})") from e

    _append_run_log(settings, app_name, run_id, inv.id, "success", duration_ms, trigger=trigger)
    app_biz.bump_use_count(settings, app_name)
    logger.info(f"invoke_app(script) success: {app_name}/{inv.id} run={run_id} {duration_ms}ms")
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
