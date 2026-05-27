"""PentaLoom FastAPI 入口.

跑法:
    cd agent
    python main.py                       # 默认, debug=False
    PENTALOOM_DEBUG=true python main.py  # 热重载

或直接 uvicorn:
    uvicorn pentaloom.server:app --reload --port 8090

端口 8090 (避开 wolfpack 的 8080). 前端 dev server 走 5273.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from pentaloom import __version__
from pentaloom.agents import ALL_AGENTS
from pentaloom.config import get_settings
from pentaloom.infra.db import Base, engine
from pentaloom.infra.loom_pool import LoomPool
from pentaloom.routers import chat, fs, health, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动初始化.

    schema 正路是 alembic upgrade head, 这里 create_all 是兜底 (alembic 跑过后 no-op).
    LoomPool 管 per-session PentaLoom 实例 (多会话并发不互阻).
    """
    settings = get_settings()
    settings.ensure_dirs()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    pool = LoomPool(agents=ALL_AGENTS)
    app.state.pool = pool
    logger.info(
        f"PentaLoom v{__version__} 启动 — host={settings.host} port={settings.port} "
        f"db={settings.db_path}"
    )
    try:
        yield
    finally:
        await pool.shutdown()
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
