"""PentaLoom FastAPI 入口.

跑法:
    python main.py                       # 默认
    PENTALOOM_DEBUG=true python main.py  # 热重载
    uvicorn pentaloom.server:app --reload --port 8090
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from pentaloom import __version__
from pentaloom.capabilities.computer import is_macos
from pentaloom.config import get_settings
from pentaloom.infra import cursor_overlay, python_env
from pentaloom.infra.db import Base, engine
from pentaloom.infra.loom_pool import LoomPool
from pentaloom.routers import (
    browser_bridge,
    chat,
    fs,
    health,
    preview,
    sessions,
    weaver,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动初始化.

    schema 正路是 alembic upgrade head, 这里 create_all 是兜底 (alembic 跑过后 no-op).
    LoomPool 管 per-session PentaLoom 实例 (多会话并发不互阻).
    python_env.prewarm 在后台跑 (uv sync + uv add 一批预装包), 不阻塞 server 启动 —
    没装完之前 install_python_libs 也能用 (uv add 自己会按需建 venv), 只是装包慢.
    cursor_overlay helper 在 macOS 上起一个常驻 subprocess, 给鼠标操作画浮层 —
    失败仅 warning, 主功能 (mouse_click / paste / screenshot) 静默继续.
    """
    settings = get_settings()
    settings.ensure_dirs()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    pool = LoomPool()
    app.state.pool = pool

    prewarm_task = asyncio.create_task(python_env.prewarm(settings))
    # 不 await — 任由后台跑. 失败 prewarm 内部已 logger.warning, 不抛.
    app.state.prewarm_task = prewarm_task

    # cursor_overlay helper: 仅 macOS 起. helper 失败 / 超时不阻断 server 启动.
    overlay_client = None
    if is_macos():
        overlay_client = await cursor_overlay.start_helper(timeout_s=5.0)
        if overlay_client is not None:
            cursor_overlay.set_active_client(overlay_client)
    else:
        logger.info("cursor_overlay skipped (not macOS)")
    app.state.cursor_overlay = overlay_client

    # M16 Phase E: bootstrap weaver app triggers (schedule + watch).
    # 必须在 yield 前装 — 否则 schedule 错过 cron 时间窗口 (server 9:00:01 启动,
    # 9:00 那次 cron 已过期没人触发). 失败仅 warning, 不阻断 server 启动.
    try:
        from pentaloom.capabilities.weaver.triggers import trigger_registry
        await trigger_registry().bootstrap(settings)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"weaver trigger bootstrap failed (server continues): {e}")

    logger.info(
        f"PentaLoom v{__version__} 启动 — host={settings.host} port={settings.port} "
        f"db={settings.db_path}"
    )
    try:
        yield
    finally:
        # 关停时若 prewarm 还在跑, 取消它. 等它干净退出再关 pool, 避免子进程残留.
        if not prewarm_task.done():
            prewarm_task.cancel()
            try:
                await prewarm_task
            except (asyncio.CancelledError, Exception):
                pass
        if overlay_client is not None:
            await cursor_overlay.shutdown_helper(overlay_client)
            cursor_overlay.set_active_client(None)
        # 让 sidebar SSE 长连优雅收尾 — 给所有订阅者推 None 让 generator break.
        from pentaloom.infra.session_status import session_status
        session_status.shutdown()
        await pool.shutdown()
        # M16 Phase E: 先停所有 trigger (schedule + watch) — 防 fire 中的 invocation
        # 撞上即将关的 service. 顺序: trigger → service.
        try:
            from pentaloom.capabilities.weaver.triggers import trigger_registry
            tn = await trigger_registry().stop_all()
            if tn > 0:
                logger.info(f"weaver: stopped {tn} trigger(s) on shutdown")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"weaver trigger stop_all failed: {e}")
        # D-2: 停所有 weaver app service (防孤儿 process). 必须在 pool.shutdown 后 —
        # 万一某个 service 是 invoke_app 时 lazy 起的, 先 pool.shutdown 让 LLM session
        # 干净退, 再清 service.
        from pentaloom.capabilities.weaver.service_registry import service_registry
        n = await service_registry().stop_all()
        if n > 0:
            logger.info(f"weaver: stopped {n} app service(s) on shutdown")
        logger.info("PentaLoom 关闭")


settings = get_settings()

app = FastAPI(
    title="PentaLoom",
    description="五瓣多 Agent 桌面助手",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["system"])
app.include_router(chat.router)
app.include_router(sessions.router)
app.include_router(fs.router)
app.include_router(preview.router)
app.include_router(weaver.router)
# Chrome 扩展 (Kro Browser Bridge) 桥接 — 路径硬编码 /chrome-bridge/{ping,ws}.
# 扩展端写死, router 内的路径也写死, 不能加 prefix.
app.include_router(browser_bridge.router, tags=["browser-bridge"])
