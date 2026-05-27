"""SessionStore 镜像表的 CRUD.

SessionStore 类只负责 SDK ABC 适配 + mtime 单调控制, 真正的 SQL 都在这里.
每个面向用户的 SDK 调用 (append/load/list/delete) 对应这里一个 module-level async fn.
"""

from typing import Any, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from claude_agent_sdk import fold_session_summary

from pentaloom.models.session import SessionEntry, SessionMtime, SessionSummary


async def append_session(
    db: AsyncSession,
    *,
    project_key: str,
    session_id: str,
    subpath: str,
    entries: Sequence[dict[str, Any]],
    mtime_ms: int,
) -> None:
    """一次写入一批 entry, 同时刷 session_mtime, 主 session 还要 fold summary."""
    max_seq_result = await db.execute(
        select(func.coalesce(func.max(SessionEntry.seq), -1)).where(
            SessionEntry.project_key == project_key,
            SessionEntry.session_id == session_id,
            SessionEntry.subpath == subpath,
        )
    )
    last_seq = int(max_seq_result.scalar_one())

    db.add_all(
        SessionEntry(
            project_key=project_key,
            session_id=session_id,
            subpath=subpath,
            seq=last_seq + 1 + i,
            uuid=e.get("uuid"),
            type=e.get("type"),
            timestamp=e.get("timestamp"),
            entry_json=e,
            mtime_ms=mtime_ms,
        )
        for i, e in enumerate(entries)
    )

    mtime_stmt = sqlite_insert(SessionMtime).values(
        project_key=project_key,
        session_id=session_id,
        subpath=subpath,
        mtime_ms=mtime_ms,
    )
    mtime_stmt = mtime_stmt.on_conflict_do_update(
        index_elements=["project_key", "session_id", "subpath"],
        set_={"mtime_ms": mtime_ms},
    )
    await db.execute(mtime_stmt)

    # subpath 不空的不是主 session, 不维护 summary
    if not subpath:
        prev_result = await db.execute(
            select(SessionSummary.data_json).where(
                SessionSummary.project_key == project_key,
                SessionSummary.session_id == session_id,
            )
        )
        prev = prev_result.scalar_one_or_none()
        key = {"project_key": project_key, "session_id": session_id}
        folded = fold_session_summary(prev, key, list(entries))
        folded["mtime"] = mtime_ms

        summary_stmt = sqlite_insert(SessionSummary).values(
            project_key=project_key,
            session_id=session_id,
            mtime_ms=mtime_ms,
            data_json=folded,
        )
        summary_stmt = summary_stmt.on_conflict_do_update(
            index_elements=["project_key", "session_id"],
            set_={"mtime_ms": mtime_ms, "data_json": folded},
        )
        await db.execute(summary_stmt)

    await db.commit()


async def load_entries(
    db: AsyncSession,
    *,
    project_key: str,
    session_id: str,
    subpath: str,
) -> Optional[list[dict[str, Any]]]:
    result = await db.execute(
        select(SessionEntry.entry_json)
        .where(
            SessionEntry.project_key == project_key,
            SessionEntry.session_id == session_id,
            SessionEntry.subpath == subpath,
        )
        .order_by(SessionEntry.seq.asc())
    )
    rows = result.scalars().all()
    return list(rows) if rows else None


async def list_main_sessions(
    db: AsyncSession, project_key: str
) -> list[tuple[str, int]]:
    """主 session (subpath='') 的 (session_id, mtime), 按 mtime 倒序."""
    result = await db.execute(
        select(SessionMtime.session_id, SessionMtime.mtime_ms)
        .where(SessionMtime.project_key == project_key, SessionMtime.subpath == "")
        .order_by(SessionMtime.mtime_ms.desc())
    )
    return [(sid, m) for sid, m in result.all()]


async def list_summaries(
    db: AsyncSession, project_key: str
) -> list[tuple[str, int, dict[str, Any]]]:
    result = await db.execute(
        select(
            SessionSummary.session_id,
            SessionSummary.mtime_ms,
            SessionSummary.data_json,
        ).where(SessionSummary.project_key == project_key)
    )
    return [(sid, m, data) for sid, m, data in result.all()]


async def delete_session(
    db: AsyncSession,
    *,
    project_key: str,
    session_id: str,
    subpath: Optional[str],
) -> None:
    """subpath=None 删整个 session (所有 subpath + summary), 否则只删指定 subpath."""
    if subpath is None:
        await db.execute(
            delete(SessionEntry).where(
                SessionEntry.project_key == project_key,
                SessionEntry.session_id == session_id,
            )
        )
        await db.execute(
            delete(SessionMtime).where(
                SessionMtime.project_key == project_key,
                SessionMtime.session_id == session_id,
            )
        )
        await db.execute(
            delete(SessionSummary).where(
                SessionSummary.project_key == project_key,
                SessionSummary.session_id == session_id,
            )
        )
    else:
        await db.execute(
            delete(SessionEntry).where(
                SessionEntry.project_key == project_key,
                SessionEntry.session_id == session_id,
                SessionEntry.subpath == subpath,
            )
        )
        await db.execute(
            delete(SessionMtime).where(
                SessionMtime.project_key == project_key,
                SessionMtime.session_id == session_id,
                SessionMtime.subpath == subpath,
            )
        )
    await db.commit()


async def list_subpaths(
    db: AsyncSession, *, project_key: str, session_id: str
) -> list[str]:
    result = await db.execute(
        select(SessionEntry.subpath)
        .where(
            SessionEntry.project_key == project_key,
            SessionEntry.session_id == session_id,
            SessionEntry.subpath != "",
        )
        .distinct()
    )
    return list(result.scalars().all())
