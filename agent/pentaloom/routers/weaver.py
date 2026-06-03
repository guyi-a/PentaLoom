"""Weaver HTTP API — sidebar / 设置页面读产物用. 工具调用走 SDK in-process MCP, 不走这.

M14 阶段实装:
  GET /weaver/products  → {skills, subagents, workflows, apps} 一次拉全, sidebar 用.
  GET /weaver/skills    → 仅 skills 列表 (含内置 + 用户织的), 兼容性单独开.

M16 加: apps 真返数据 (含 invocation_count + 是否有 app.json + components 摘要).

Phase C-0 加: GET /weaver/apps/{name}/window  → 返 window HTML 给 Electron BrowserWindow
loadURL. 不做 sandbox (sandbox 由 BrowserWindow contextIsolation + preload 白名单管),
只校 name + entry path traversal.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from pentaloom.capabilities.weaver import app as app_biz
from pentaloom.capabilities.weaver import app_runtime, index, paths, skill
from pentaloom.capabilities.weaver.window_registry import window_registry
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


class WeaverProductsResponse(BaseModel):
    skills: list[SkillSummary]
    subagents: list[Any] = []   # M17 实装
    workflows: list[Any] = []   # M16 workflow milestone
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


@router.get("/skills", response_model=list[SkillSummary])
def list_skills() -> list[SkillSummary]:
    return _collect_skills()


@router.get("/products", response_model=WeaverProductsResponse)
def list_products() -> WeaverProductsResponse:
    """sidebar 一次拉全部产物. M14/M16 阶段 subagents/workflows 仍空."""
    return WeaverProductsResponse(
        skills=_collect_skills(),
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

    return {
        "summary": summary,
        "files": files_detail,
        "meta": meta_dict,
        "recent_runs": recent_runs,
    }


class InvokeAppRequest(BaseModel):
    """POST /weaver/apps/{name}/invoke 入参. window runtime 内 fetch 用."""

    invocation_id: str
    args: dict[str, Any] = {}


@router.post("/apps/{name}/invoke", response_model=dict)
async def invoke_app_http(name: str, body: InvokeAppRequest) -> dict:
    """HTTP 调 invocation — 给 weaver app window 内 fetch 用 (Phase C-1).

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


@router.websocket("/apps/{name}/window-ws")
async def app_window_ws(websocket: WebSocket, name: str) -> None:
    """window 启动时 preload 用 WebSocket 连过来注册 self (Phase C-2).

    协议 (单 ws 复用所有 request_id):
      Client → Server:
        {type: "ready", window_id: "<uuid>"}        # 可选 hello
        {type: "invoke_result", request_id, output} # window handler 返成功
        {type: "invoke_error",  request_id, error}  # window handler 抛错

      Server → Client:
        {type: "invoke", request_id, invocation_id, args}
                                                    # agent invoke_app target=window 触发

    安全: 不校 app 是否 ready / 是否 trusted — window 能开就说明 status=ready 过, 且
    preload 闭包死 app name. ws 路径里的 name 必须跟 preload 连过来时一致 (preload 用
    BrowserWindow loadURL 的 name 拼 ws url, 跨 app 不可能).
    """
    settings = get_settings()
    entry_app = index.find_entry(settings, "app", name)
    if entry_app is None:
        await websocket.close(code=4404, reason=f"app not found: {name}")
        return

    await websocket.accept()
    reg = window_registry()
    reg.add(name, websocket)
    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype in ("invoke_result", "invoke_error"):
                rid = msg.get("request_id")
                if rid:
                    reg.resolve(rid, msg)
            # ready / 其他 type 忽略
    except WebSocketDisconnect:
        pass
    finally:
        reg.remove(name, websocket)
        reg.drop_pending_for_ws(websocket)


@router.get("/apps/{name}/window", response_class=HTMLResponse)
def serve_app_window(name: str, entry: str | None = None) -> Response:
    """Serve weaver app 的 window HTML 给 Electron BrowserWindow loadURL.

    Phase C-0 spike: 只支持 .html entry (vanilla HTML, 不做 TSX 编译).
    entry 不传 → 自动找 app.json 第一个 window component 的 entry.

    安全:
      - app 必须 status=ready (跟 invoke_app 一致, 防 draft/failed 露出来)
      - entry 走 _resolve_within_files (绝对禁止跨出 files/)
      - 强制 .html 后缀
      - HTML 原样返 (不做模板渲染), CORS 允许 same-origin (Electron 不需要 CORS 但
        Vite dev 调试时需要)
    """
    settings = get_settings()
    entry_app = index.find_entry(settings, "app", name)
    if entry_app is None:
        raise HTTPException(404, f"app not found: {name}")

    meta = app_biz.read_meta(settings, name)
    if meta is None or meta.status != "ready":
        raise HTTPException(
            409, f"app {name!r} status={meta.status if meta else 'missing'}, "
            "must be 'ready' (finalize first)"
        )

    # 选 entry: 显式传 / app.json 第一个 window / 默认 index.html
    if entry is None:
        app_def = app_biz.read_app_definition(settings, name)
        if app_def and app_def.components.windows:
            entry = app_def.components.windows[0].entry
        else:
            entry = "index.html"

    if not entry.endswith(".html"):
        raise HTTPException(400, f"entry must be .html for Phase C-0; got {entry}")

    from pentaloom.capabilities.weaver.app import _resolve_within_files

    files_root = paths.app_files_dir(settings, name)
    try:
        target = _resolve_within_files(files_root, entry, label="window.entry")
    except index.WeaverError as e:
        raise HTTPException(400, str(e)) from e

    if not target.exists():
        raise HTTPException(404, f"entry file not found: {entry}")

    html = target.read_text()
    # Electron loadURL 同 origin 不需要 CORS, 但 dev 跨 Vite 调试方便也加上
    headers = {
        "Cache-Control": "no-store",  # weave 期间频繁改, 别缓存
        "X-Frame-Options": "SAMEORIGIN",
    }
    return HTMLResponse(html, headers=headers)


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
