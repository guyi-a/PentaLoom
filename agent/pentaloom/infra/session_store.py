"""SDK SessionStore ABC 的 SQLite 实现.

类只负责:
  - 适配 SDK 调用 (SessionKey/SessionStoreEntry... 拆字段)
  - mtime 单调控制 (本进程内不会回退)

真正的 SQL 都在 crud.session.
schema 不在这里建, 由 alembic 管.
"""

import time

from claude_agent_sdk import (
    SessionKey,
    SessionListSubkeysKey,
    SessionStore,
    SessionStoreEntry,
    SessionStoreListEntry,
    SessionSummaryEntry,
)

from pentaloom.crud import session as crud_session
from pentaloom.infra.db import AsyncSessionLocal


class SQLiteSessionStore(SessionStore):
    def __init__(self) -> None:
        self._last_mtime = 0

    async def __aenter__(self) -> "SQLiteSessionStore":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def _next_mtime(self) -> int:
        now_ms = int(time.time() * 1000)
        if now_ms <= self._last_mtime:
            now_ms = self._last_mtime + 1
        self._last_mtime = now_ms
        return now_ms

    async def append(self, key: SessionKey, entries: list[SessionStoreEntry]) -> None:
        mtime = self._next_mtime()
        async with AsyncSessionLocal() as db:
            await crud_session.append_session(
                db,
                project_key=key["project_key"],
                session_id=key["session_id"],
                subpath=key.get("subpath") or "",
                entries=entries,
                mtime_ms=mtime,
            )

    async def load(self, key: SessionKey) -> list[SessionStoreEntry] | None:
        async with AsyncSessionLocal() as db:
            return await crud_session.load_entries(
                db,
                project_key=key["project_key"],
                session_id=key["session_id"],
                subpath=key.get("subpath") or "",
            )

    async def list_sessions(self, project_key: str) -> list[SessionStoreListEntry]:
        async with AsyncSessionLocal() as db:
            rows = await crud_session.list_main_sessions(db, project_key)
        return [{"session_id": sid, "mtime": m} for sid, m in rows]

    async def list_session_summaries(self, project_key: str) -> list[SessionSummaryEntry]:
        async with AsyncSessionLocal() as db:
            rows = await crud_session.list_summaries(db, project_key)
        results = []
        for sid, m, data in rows:
            d = dict(data)
            d["session_id"] = sid
            d["mtime"] = m
            results.append(d)
        return results

    async def delete(self, key: SessionKey) -> None:
        async with AsyncSessionLocal() as db:
            await crud_session.delete_session(
                db,
                project_key=key["project_key"],
                session_id=key["session_id"],
                subpath=key.get("subpath"),
            )

    async def list_subkeys(self, key: SessionListSubkeysKey) -> list[str]:
        async with AsyncSessionLocal() as db:
            return await crud_session.list_subpaths(
                db,
                project_key=key["project_key"],
                session_id=key["session_id"],
            )
