"""Invocable App 调用执行器.

invoke_app(app_name, invocation_id, args) → {run_id, status, duration_ms, output}:
  1. 读 manifest + app.json
  2. 找 invocation_id + target
  3. JSON Schema 校验 input
  4. 按 target.component 分发到 script / service / window 路径
  5. parse stdout JSON, JSON Schema 校验 output
  6. 写 InvocationRun 日志到 logs/runs.jsonl (单文件 append, 简单 query)
  7. 返 {run_id, status, duration_ms, output} — output 是 handler 实际返的;
     run_id / duration_ms 给 agent 排查日志 + 反馈用户耗时. handler 输出包在
     output 里, 不平铺到 top-level 是为了不跟 run_id 等元字段名字撞.
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

    status: success / failed / skipped.
    trigger: user (default, MCP tool / window WS 调用) / schedule / watch / workflow
    (workflow_runtime.invoke_workflow 调用 invoke_app step 时透传).

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
    """调一个 invocable app 的 invocation.

    trigger: user (MCP tool / window WS, default) / schedule (cron 触发) /
    watch (fs event 触发). 透传到 _invoke_*, runs.jsonl 落地以区分来源.

    返 {run_id, status, duration_ms, output}. 失败抛 InvokeError (附 run_id 在 message 里).
    """
    args = args or {}
    started_at_ms = int(datetime.utcnow().timestamp() * 1000)
    run_id = _new_run_id()

    # ─── 1. 读 manifest + app.json + meta ──────────────────────────────
    try:
        manifest = app_biz.read_manifest(settings, app_name)
    except index.WeaverError as e:
        raise InvokeError(f"manifest 读失败 (run {run_id}): {e}") from e

    app_def = app_biz.read_app_definition(settings, app_name)
    if app_def is None:
        raise InvokeError(
            f"app {app_name!r} 缺 app.json (invoke_app 必填)"
        )

    # 只允许 finalize 通过的 app 被 invoke.
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
            f"invocation {invocation_id!r} 缺 target (invoke_app 必填)"
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
    """target.component='window' 路径 — 走 loom socket 调 JS 端注册的 handler.

    路径:
      Python → loom socket window.invoke → loom 找 (app, window_name) 子进程
      → loomer stdin NDJSON → JS handler (window.pentaloom.registerInvocation)
      → JS handler async 返值 → loomer stdout NDJSON → loom → socket → 这里

    window 必须先开 (用户在 sidebar 点 Open Window 或 loomctl open). 不主动 open
    — 避免 agent 隐式弹窗的 UX 突兀.

    timeout: 走 inv.timeout_ms (manifest 声明), loom 端 select 超时返 error 但
    **不 kill loomer** — handler 慢不该让窗死.
    """
    from pentaloom.infra import loom_client

    _validate_input(args, inv.input_schema)

    window_name = inv.target.name
    timeout_s = max(1, inv.timeout_ms // 1000)
    logger.info(
        f"invoke_app(window): {app_name}/{inv.id} run={run_id} "
        f"window_name={window_name} timeout={timeout_s}s"
    )

    try:
        output = await loom_client.invoke_window(
            app_name, window_name, inv.id, args,
            timeout_s=float(timeout_s),
        )
    except loom_client.LoomCommandFailed as e:
        # window 没开就自己开一次再 retry 一次 — 用户主动 invoke 时不该要求"先点
        # Open Window 再来", 该 agent 自救. retry 1 次, 还不行就抛.
        if "no window with" in str(e).lower():
            logger.info(
                f"invoke_app(window): {app_name}/{window_name} 没开, 自动 open + retry"
            )
            try:
                await app_biz.open_window_for_app(
                    settings, app_name, window_name=window_name,
                )
                # window JS bundle 加载 + handler 注册需要一点时间; loom_client
                # invoke 内部已经有 timeout, 短轮询等 1s 让 JS register 一下
                # (实测 esm.sh 冷启动 ~600ms, 缓存后 100ms 内).
                import asyncio as _aio
                await _aio.sleep(1.0)
                output = await loom_client.invoke_window(
                    app_name, window_name, inv.id, args,
                    timeout_s=float(timeout_s),
                )
            except (loom_client.LoomError, Exception) as retry_err:
                duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms
                err = (
                    f"window {app_name}/{window_name} 自动开窗 + retry 仍失败. "
                    f"retry 错: {retry_err}; 原始错: {e}"
                )
                _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
                raise InvokeError(f"{err} (run {run_id})") from retry_err
        else:
            duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms
            msg = str(e)
            if "未注册" in msg or "not registered" in msg.lower():
                err = (
                    f"window {app_name}/{window_name} 没注册 invocation {inv.id!r} handler. "
                    f"window TSX 里加 window.pentaloom.registerInvocation({inv.id!r}, "
                    f"async (args) => {{...}}). 原始错: {msg}"
                )
            else:
                err = f"window invoke failed: {msg}"
            _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
            raise InvokeError(f"{err} (run {run_id})") from e
    except loom_client.LoomUnavailable as e:
        duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms
        err = (
            f"loom daemon 没起 — 跑 `make loom-install` 装系统级 daemon. "
            f"原始错: {e}"
        )
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})") from e
    except loom_client.LoomError as e:
        duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms
        err = f"loom 协议错: {e}"
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, err, trigger=trigger)
        raise InvokeError(f"{err} (run {run_id})") from e

    duration_ms = int(datetime.utcnow().timestamp() * 1000) - started_at_ms

    try:
        _validate_output(output, inv.output_schema)
    except InvokeError as e:
        _append_run_log(settings, app_name, run_id, inv.id, "failed", duration_ms, str(e), trigger=trigger)
        raise InvokeError(f"{e} (run {run_id})") from e

    _append_run_log(settings, app_name, run_id, inv.id, "success", duration_ms, trigger=trigger)
    app_biz.bump_use_count(settings, app_name)
    logger.info(
        f"invoke_app(window) success: {app_name}/{inv.id} run={run_id} {duration_ms}ms"
    )
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
    """target.component='service' 路径 — service 由 launchd KeepAlive 常驻, HTTP fetch.

    service 不由 invoke 路径启动. declared service 由 launchd 启动, ephemeral service
    由 weave_service_start 启动; 两者都会把端口写到 .runtime/<svc>.port.
    这里只读端口文件并发起 HTTP 请求.
    """
    import httpx

    from pentaloom.weaver_runner import read_port_file

    _validate_input(args, inv.input_schema)

    # target 必须含 method + path (path validator 已校 / 开头 + 拒 ://)
    method = (inv.target.method or "POST").upper()
    path = inv.target.path or "/"
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise InvokeError(
            f"target.method 只支持 GET/POST/PUT/PATCH/DELETE, 收到 {method!r}"
        )

    port = read_port_file(settings, app_name, inv.target.name)
    if port is None:
        # service 没启过 / .runtime/<svc>.port 不在 → 大概率没 finalize 或 launchd
        # 没装. 让用户检查.
        raise InvokeError(
            f"service {inv.target.name!r} 端口文件不在 .runtime/ — service 没起. "
            f"检查: 1) make loom-install 装系统级 daemon; "
            f"2) weave_app_finalize 重新写 plist; 3) launchctl list | grep "
            f"com.pentaloom.app.{app_name} 看 plist 状态. (run {run_id})"
        )

    timeout_s = max(1, inv.timeout_ms // 1000)
    url = f"http://127.0.0.1:{port}{path}"
    logger.info(
        f"invoke_app(service): {app_name}/{inv.id} run={run_id} {method} {url} timeout={timeout_s}s"
    )

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            if method in ("GET", "DELETE"):
                # GET / DELETE: args 当 query (扁平 string 化). RESTful 习惯不带 body.
                params = {k: str(v) for k, v in args.items()}
                http_resp = await client.request(method, url, params=params)
            else:
                # POST / PUT / PATCH: args 当 JSON body.
                http_resp = await client.request(method, url, json=args)
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
    from pentaloom.capabilities.weaver.app_env import weaver_app_env
    extra_env = weaver_app_env(
        settings, app_name, invocation_id=inv.id, trigger=trigger,
    )
    result = await python_env.run_app_script(
        settings,
        cwd=cwd,
        command=script.command,
        stdin_data=stdin_payload,
        timeout=timeout_s,
        extra_env=extra_env,
        # workdir 进了子目录时, uv --project 仍要找 files_root 的 pyproject.
        app_files_root=files_root,
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
