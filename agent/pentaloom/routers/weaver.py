"""Weaver HTTP API — sidebar / 设置页面读产物用. 工具调用走 SDK in-process MCP, 不走这.

端点:
  GET /weaver/products  → {skills, subagents, workflows, apps} 一次拉全, sidebar 用.
  GET /weaver/skills    → 仅 skills 列表 (含内置 + 用户织的), 兼容性单独开.
  apps 返数据 (含 invocation_count + 是否有 app.json + components 摘要).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pentaloom.capabilities.weaver import app as app_biz
from pentaloom.capabilities.weaver import app_runtime, index, paths, skill
from pentaloom.capabilities.weaver import workflow as workflow_biz
from pentaloom.capabilities.weaver import workflow_runtime
from pentaloom.config import get_settings

router = APIRouter(prefix="/weaver", tags=["weaver"])


class SkillSummary(BaseModel):
    name: str
    description: str
    source: str  # "builtin" | "agent_woven" | "user_imported" | "user_handwritten"


class AppSummary(BaseModel):
    """Sidebar 用的 app 摘要. 不返完整 manifest (太大), 关键 metric 够展示."""

    name: str
    description: str
    source: str
    status: str  # draft | ready | dirty | failed (递进式 weave 状态机, GPT 收口)
    invocation_count: int
    has_app_definition: bool
    component_counts: dict[str, int]  # {scripts: 2, windows: 1, ...}


class WorkflowSummary(BaseModel):
    """dynamic workflow 摘要 — sidebar 用. 关键 metric 是步数 + status + use_count."""

    name: str
    description: str
    source: str
    status: str  # draft | ready | dirty | failed (跟 app 同款状态机)
    step_count: int
    use_count: int


class WeaverProductsResponse(BaseModel):
    skills: list[SkillSummary]
    subagents: list[Any] = []   # 占位, 待实装
    workflows: list[WorkflowSummary] = []
    apps: list[AppSummary]


def _collect_skills() -> list[SkillSummary]:
    """Sidebar 只看用户产物 — 内置 skill (report-generator 等) 是 PentaLoom 出厂能力,
    不能改 / 不能删, 在 sidebar 显示反而是噪音. agent 端 list_weaver / inspect_weaver
    仍然支持读内置 (防 agent weave 重名), 但 frontend 这条只走用户产物.
    """
    settings = get_settings()
    idx = index.load_index(settings)
    return [
        SkillSummary(name=e.name, description=e.description, source=e.source)
        for e in idx.skills
    ]


def _collect_apps() -> list[AppSummary]:
    """Sidebar 用户 weave 的 app 列表. 单个 app 的 manifest 读失败不阻塞全列表,
    log warn + skip (manifest 损坏的 app 也会在 list 里露出, 但 component_counts
    全 0; 用户从 list 里看出哪个坏)."""
    settings = get_settings()
    idx = index.load_index(settings)
    out: list[AppSummary] = []
    for e in idx.apps:
        try:
            manifest = app_biz.read_manifest(settings, e.name)
            app_def = app_biz.read_app_definition(settings, e.name)
            meta = app_biz.read_meta(settings, e.name)
        except index.WeaverError:
            # 损坏 app 也展示 (零 count), 不让一个挂的 app 屏蔽整个 list
            out.append(AppSummary(
                name=e.name, description=e.description, source=e.source,
                status="failed",  # 读不出来当 failed
                invocation_count=0, has_app_definition=False, component_counts={},
            ))
            continue
        counts: dict[str, int] = {}
        if app_def is not None:
            counts = {
                "windows": len(app_def.components.windows),
                "services": len(app_def.components.services),
                "scripts": len(app_def.components.scripts),
                "schedules": len(app_def.components.schedules),
                "watches": len(app_def.components.watches),
            }
        out.append(AppSummary(
            name=e.name, description=e.description, source=e.source,
            status=meta.status if meta else "draft",
            invocation_count=len(manifest.invocations),
            has_app_definition=app_def is not None,
            component_counts=counts,
        ))
    return out


def _collect_workflows() -> list[WorkflowSummary]:
    """Sidebar 用户 weave 的 workflow 列表. 单个 workflow 读失败不阻塞全列表 — 跟
    _collect_apps 同款降级 (损坏的 workflow 也露出, step_count=0 当 failed)."""
    settings = get_settings()
    idx = index.load_index(settings)
    out: list[WorkflowSummary] = []
    for e in idx.workflows:
        try:
            definition = workflow_biz.read_workflow(settings, e.name)
            meta = workflow_biz.read_meta(settings, e.name)
        except index.WeaverError:
            out.append(WorkflowSummary(
                name=e.name, description=e.description, source=e.source,
                status="failed", step_count=0, use_count=0,
            ))
            continue
        out.append(WorkflowSummary(
            name=e.name, description=e.description, source=e.source,
            status=meta.status if meta else "draft",
            step_count=len(definition.steps),
            use_count=meta.use_count if meta else 0,
        ))
    return out


@router.get("/skills", response_model=list[SkillSummary])
def list_skills() -> list[SkillSummary]:
    return _collect_skills()


@router.get("/products", response_model=WeaverProductsResponse)
def list_products() -> WeaverProductsResponse:
    """sidebar 一次拉全部产物."""
    return WeaverProductsResponse(
        skills=_collect_skills(),
        workflows=_collect_workflows(),
        apps=_collect_apps(),
    )


@router.get("/skills/{name}/content", response_model=dict)
def read_skill_content(name: str) -> dict:
    """读单个 skill 的完整 SKILL.md 内容. 内置 + 用户织的都支持读 (agent 端 inspect_weaver
    也走这条; sidebar 现在不显示 builtin, 但 agent 想 inspect 内置还是可以)."""
    settings = get_settings()
    builtin = next(
        (b for b in skill.builtin_skills_summary() if b["name"] == name), None
    )
    if builtin is not None:
        from pentaloom.capabilities.weaver import paths
        skill_md = paths.builtin_skill_md(name)
        return {
            "name": name,
            "source": "builtin",
            "description": builtin["description"],
            "content": skill_md.read_text(),
        }
    entry = index.find_entry(settings, "skill", name)
    if entry is None:
        raise HTTPException(404, f"skill not found: {name}")
    try:
        content = skill.read_skill_md(settings, name)
    except index.WeaverError as e:
        raise HTTPException(500, str(e)) from e
    return {
        "name": name,
        "source": entry.source,
        "description": entry.description,
        "content": content,
    }


@router.get("/apps/{name}/detail", response_model=dict)
def read_app_detail(name: str) -> dict:
    """Sidebar AppDetailPanel 用 — 一次拉全 detail 所需数据.

    返:
      - summary: manifest_invocations_summary (manifest + components + relative files)
      - files: [{rel_path, absolute_path, ext, size}] — 前端用 absolute_path 调
        openFile, ext / size 给 UI 展示
      - meta: status / updated_at / last_finalized_at / last_finalize_error
      - recent_runs: 最近 20 条 logs/runs.jsonl
    """
    settings = get_settings()
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise HTTPException(404, f"app not found: {name}")
    try:
        summary = app_biz.manifest_invocations_summary(settings, name)
    except index.WeaverError as e:
        raise HTTPException(500, str(e)) from e

    files_root = paths.app_files_dir(settings, name)
    files_detail: list[dict] = []
    for rel in summary.get("files", []):
        abs_path = files_root / rel
        try:
            size = abs_path.stat().st_size if abs_path.exists() else 0
        except OSError:
            size = 0
        ext = abs_path.suffix.lstrip(".") if abs_path.suffix else ""
        files_detail.append({
            "rel_path": rel,
            "absolute_path": str(abs_path),
            "ext": ext,
            "size": size,
        })

    meta = app_biz.read_meta(settings, name)
    meta_dict = meta.model_dump(mode="json") if meta else None

    recent_runs = app_runtime.tail_run_logs(settings, name, limit=20)

    # service / schedule / watch 三类全走 launchd. 一次性读 launchctl status.
    from pentaloom.capabilities.weaver import launchd_plist
    plist_states = launchd_plist.list_for_app(name)

    # 织造期声明 (cron / invocation_id / path) 必须从 app.json join, launchctl 不知道.
    app_def = app_biz.read_app_definition(settings, name)
    sched_specs = {s.name: s for s in (app_def.components.schedules if app_def else [])}
    watch_specs = {w.name: w for w in (app_def.components.watches if app_def else [])}

    # 倒读 runs.jsonl 推 last_fired_at — 一次读完按 (invocation_id, trigger) 索引.
    last_fired = _last_fired_index(settings, name)

    # 拆分三类给前端 modal 用 (字段命名跟原来兼容: running_services / triggers).
    running_services = [
        _service_row(settings, name, s) for s in plist_states if s["kind"] == "svc"
    ]
    triggers_state = {
        "schedules": [
            _schedule_row(s, sched_specs.get(s["comp_name"]), last_fired)
            for s in plist_states if s["kind"] == "sched"
        ],
        "watches": [
            _watch_row(s, watch_specs.get(s["comp_name"]), last_fired)
            for s in plist_states if s["kind"] == "wch"
        ],
    }

    # ephemeral service (memory-only, agent 织造期 weave_service_start 起的).
    # 跟 declared running_services 并列, 前端区分两类 section.
    from pentaloom.capabilities.weaver import ephemeral_service
    ephemeral_services = [
        s.to_dict() for s in ephemeral_service.get_registry().list_for_app(name)
    ]

    return {
        "summary": summary,
        "files": files_detail,
        "meta": meta_dict,
        "recent_runs": recent_runs,
        "running_services": running_services,
        "ephemeral_services": ephemeral_services,
        "triggers": triggers_state,  # {schedules: [...], watches: [...]}
    }


def _service_row(settings, app_name: str, s: dict) -> dict:
    """svc plist + .runtime/<svc>.port + psutil ctime → 前端 ServiceRow 字段."""
    pid = s["pid"]
    started_at = None
    uptime_seconds = None
    if pid:
        try:
            import psutil
            proc = psutil.Process(pid)
            started_at = int(proc.create_time())
            import time
            uptime_seconds = max(0, int(time.time() - started_at))
        except Exception:
            pass  # 进程 race 没了 / psutil 拒访问 — 字段留 None
    return {
        "name": s["comp_name"],
        "status": "running" if s["loaded"] and pid else "dead",
        "pid": pid,
        "last_exit_status": s["last_exit_status"],
        "port": _read_service_port(settings, app_name, s["comp_name"]),
        "log_path": s.get("stdout_path"),
        "started_at": started_at,
        "uptime_seconds": uptime_seconds,
    }


def _schedule_row(s: dict, spec, last_fired: dict) -> dict:
    """sched plist + app.json spec + runs.jsonl → 前端 ScheduleRow 字段.

    next_fire_at 用 croniter 算; in_flight 推断: loaded + pid != None
    (schedule 通常秒级跑完, in_flight 持续时间短, 这粗判够用).
    """
    name = s["comp_name"]
    cron_expr = spec.schedule if spec else ""
    invocation_id = spec.invocation_id if spec else ""
    next_fire_at = None
    if cron_expr:
        try:
            from croniter import croniter
            from datetime import datetime
            next_fire_at = int(croniter(cron_expr, datetime.now()).get_next())
        except Exception:
            pass
    return {
        "name": name,
        "schedule": cron_expr,
        "invocation_id": invocation_id,
        "loaded": s["loaded"],
        "pid": s["pid"],
        "last_exit_status": s["last_exit_status"],
        "last_fired_at": last_fired.get((invocation_id, "schedule")),
        "next_fire_at": next_fire_at,
        "in_flight": bool(s["loaded"] and s["pid"]),
        "log_path": s.get("stdout_path"),
    }


def _watch_row(s: dict, spec, last_fired: dict) -> dict:
    """wch plist + app.json spec + runs.jsonl → 前端 watch row 字段."""
    name = s["comp_name"]
    invocation_id = spec.invocation_id if spec else None
    return {
        "name": name,
        "path": spec.path if spec else "",
        "invocation_id": invocation_id or "",
        "loaded": s["loaded"],
        "pid": s["pid"],
        "last_exit_status": s["last_exit_status"],
        "last_fired_at": last_fired.get((invocation_id, "watch")) if invocation_id else None,
        "in_flight": bool(s["loaded"] and s["pid"]),
        "log_path": s.get("stdout_path"),
    }


def _last_fired_index(settings, app_name: str) -> dict:
    """读 runs.jsonl 反推 (invocation_id, trigger) → last fired unix ts.

    只看 schedule / watch trigger, 倒读至多最近 200 条够用 — 老条目按 invocation 名
    覆盖即可, 内存 dict last-write-wins.
    """
    from datetime import datetime
    runs = app_runtime.tail_run_logs(settings, app_name, limit=200)
    out: dict[tuple[str, str], int] = {}
    for r in runs:
        trig = r.get("trigger", "user")
        if trig not in ("schedule", "watch"):
            continue
        inv = r.get("invocation_id", "")
        ts_iso = r.get("started_at", "")
        if not inv or not ts_iso:
            continue
        try:
            # started_at 格式: ISO-like + Z 后缀
            dt = datetime.fromisoformat(ts_iso.rstrip("Z"))
            out[(inv, trig)] = int(dt.timestamp())
        except ValueError:
            continue
    return out


def _read_service_port(settings, app_name: str, svc_name: str) -> int | None:
    """读 .runtime/<svc>.port 文件 — service wrapper 启动时写的端口."""
    from pentaloom.weaver_runner import read_port_file
    return read_port_file(settings, app_name, svc_name)


@router.get("/workflows/{name}/detail", response_model=dict)
def read_workflow_detail(name: str) -> dict:
    """WorkflowDetailModal 用 — 一次拉全 detail.

    返:
      - summary: {name, description, version, step_count, steps_summary, definition, meta}
      - recent_runs: 最近 20 条 logs/runs.jsonl (含每步 status/duration/output_summary)
    """
    settings = get_settings()
    entry = index.find_entry(settings, "workflow", name)
    if entry is None:
        raise HTTPException(404, f"workflow not found: {name}")
    try:
        summary = workflow_biz.list_workflow_summary(settings, name)
    except index.WeaverError as e:
        raise HTTPException(500, str(e)) from e
    recent_runs = workflow_runtime.tail_workflow_run_logs(settings, name, limit=20)
    return {
        "summary": summary,
        "recent_runs": recent_runs,
    }


class InvokeAppRequest(BaseModel):
    """POST /weaver/apps/{name}/invoke 入参. window runtime 内 fetch 用."""

    invocation_id: str
    args: dict[str, Any] = {}


@router.post("/apps/{name}/invoke", response_model=dict)
async def invoke_app_http(name: str, body: InvokeAppRequest) -> dict:
    """HTTP 调 invocation — 给 weaver app window 内 fetch 用.

    跟 SDK MCP 工具 invoke_app 走同一份 runtime (app_runtime.invoke_app), 但不走 HITL —
    用户已经主动打开了这个 window, 默认信任 (跟 web_search / browser_bridge 同款"会话级 enabled").

    校验复用 runtime 内的: app 存在 / status=ready / invocation_id 存在 / input schema / output schema.
    """
    settings = get_settings()
    entry_app = index.find_entry(settings, "app", name)
    if entry_app is None:
        raise HTTPException(404, f"app not found: {name}")
    try:
        result = await app_runtime.invoke_app(
            settings,
            app_name=name,
            invocation_id=body.invocation_id,
            args=body.args or {},
        )
    except app_runtime.InvokeError as e:
        raise HTTPException(400, str(e)) from e
    except index.WeaverError as e:
        raise HTTPException(500, str(e)) from e
    return result


@router.post("/apps/{name}/window/open", response_model=dict)
async def open_app_window(name: str) -> dict:
    """打开 invocable app 的 window — 走 loom daemon spawn loomer 子进程.

    返 {"window_id": str, "pid": int}; loom daemon 没起返 503 + 提示装命令.
    """
    settings = get_settings()
    from pentaloom.infra import loom_client
    try:
        return await app_biz.open_window_for_app(settings, name)
    except index.WeaverError as e:
        msg = str(e)
        if "loom daemon 没起" in msg:
            raise HTTPException(503, msg) from e
        if "不存在" in msg:
            raise HTTPException(404, msg) from e
        raise HTTPException(400, msg) from e
    except loom_client.LoomError as e:
        raise HTTPException(500, f"loom call failed: {e}") from e


@router.get("/apps/{name}/watches/{watch_name}/files", response_model=dict)
def list_app_watch_files(name: str, watch_name: str) -> dict:
    """列出 watch component 暴露的目录里的文件清单. 给 sidebar
    AppDetailModal 的 Watches section lazy fetch 用.

    安全:
      - watch.path 必须相对, 不能 ..
      - 只允许两类前缀: 'files/' 或 'runs/' (script 产物常在 runs/<run_id>/, 服务/window
        资产在 files/. 别的子目录 manifest/app.json/meta.json/logs/ 都拒)
      - resolve 后 is_relative_to(app_dir)
      - 单次最多返 500 个 entry (防超大目录卡 UI)
      - 返 rel_path + absolute_path + size + mtime + is_dir. absolute_path 给前端
        WatchEntryRow 的 openFile 用 (跟 AppFileEntry 一致的契约), 后端已校 resolve
        在 app_dir 内, 给前端不算泄露多余信息
    """
    settings = get_settings()
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise HTTPException(404, f"app not found: {name}")

    app_def = app_biz.read_app_definition(settings, name)
    if app_def is None:
        raise HTTPException(409, f"app {name!r} 缺 app.json")
    watch = next((w for w in app_def.components.watches if w.name == watch_name), None)
    if watch is None:
        raise HTTPException(
            404,
            f"watch {watch_name!r} 不在 app.json (可用: "
            f"{[w.name for w in app_def.components.watches]})"
        )

    raw_path = (watch.path or "").strip()
    if not raw_path:
        raise HTTPException(400, f"watch {watch_name!r} path 为空")
    if raw_path.startswith("/") or ".." in raw_path.split("/"):
        raise HTTPException(400, f"watch.path 不允许跳出 app 目录: {raw_path!r}")
    head = raw_path.split("/", 1)[0]
    if head not in ("files", "runs"):
        raise HTTPException(
            400,
            f"watch.path 必须以 'files/' 或 'runs/' 开头, 收到 {raw_path!r}"
        )

    app_root = paths.app_dir(settings, name)
    target_dir = (app_root / raw_path).resolve()
    try:
        target_dir.relative_to(app_root.resolve())
    except ValueError:
        raise HTTPException(400, f"watch.path 越出 app 目录") from None

    if not target_dir.exists():
        return {
            "name": name,
            "watch": watch_name,
            "path": raw_path,
            "entries": [],
            "truncated": False,
            "note": f"目录还不存在 ({raw_path}) — script/service 还没产文件",
        }
    if not target_dir.is_dir():
        raise HTTPException(400, f"watch.path 不是目录: {raw_path}")

    LIMIT = 500
    entries: list[dict] = []
    truncated = False
    for p in sorted(target_dir.rglob("*")):
        if not p.exists():
            continue
        rel = p.relative_to(target_dir).as_posix()
        try:
            stat = p.stat()
        except OSError:
            continue
        entries.append({
            "rel_path": rel,
            "absolute_path": str(p),
            "size": stat.st_size if p.is_file() else 0,
            "mtime": stat.st_mtime,
            "is_dir": p.is_dir(),
        })
        if len(entries) >= LIMIT:
            truncated = True
            break

    return {
        "name": name,
        "watch": watch_name,
        "path": raw_path,
        "entries": entries,
        "truncated": truncated,
    }


@router.post("/apps/{name}/services/{service_name}/stop", status_code=200)
def stop_app_service(name: str, service_name: str) -> dict:
    """停一个 service plist (launchctl unload). 不删 plist 文件, 用户改完
    重新 finalize 时会再 load.

    service 由 launchd KeepAlive 监督, stop = launchctl unload. 想"重启"
    就再 finalize 一次 (reload_for_app 会 unload + 重写 + load). 没单独的 restart
    endpoint — finalize 是单一入口.
    """
    settings = get_settings()
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise HTTPException(404, f"app not found: {name}")

    app_def = app_biz.read_app_definition(settings, name)
    if app_def is None:
        raise HTTPException(409, f"app {name!r} 缺 app.json")
    svc = next(
        (s for s in app_def.components.services if s.name == service_name), None
    )
    if svc is None:
        raise HTTPException(
            404,
            f"service {service_name!r} 不在 app.json (可用: "
            f"{[s.name for s in app_def.components.services]})"
        )

    from pentaloom.capabilities.weaver import launchd_plist
    stopped = launchd_plist.stop_component(name, "svc", service_name)
    return {"name": name, "service": service_name, "stopped": stopped}


@router.get("/apps/{name}/manifest", response_model=dict)
def read_app_manifest(name: str) -> dict:
    """Sidebar 详情 / 后续 invoke UI 用 — 返完整 manifest + components + files 列表."""
    settings = get_settings()
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise HTTPException(404, f"app not found: {name}")
    try:
        return app_biz.manifest_invocations_summary(settings, name)
    except index.WeaverError as e:
        raise HTTPException(500, str(e)) from e
