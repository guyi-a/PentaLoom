"""SQLAlchemy 2.0 async engine + 会话工厂.

桌面单进程, aiosqlite 足够.

schema 演进走 alembic (agent/alembic/), 不在代码里 create_all.
开发流程:
  1. 加 model 到 pentaloom/models/
  2. cd agent && alembic revision --autogenerate -m "<msg>"
  3. alembic upgrade head
"""

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from pentaloom.config import get_settings

settings = get_settings()
settings.ensure_dirs()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """所有 SQLAlchemy 表的基类."""
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
