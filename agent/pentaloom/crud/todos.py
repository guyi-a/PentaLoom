"""session_todos CRUD.

todos 整体存 JSON 字符串, 4 个操作:
  - write_todos: 整体覆盖, 自动 seq=1..N
  - update_todo: 改单条 (status / content / activeForm 任一)
  - read_todos: 读当前列表
  - delete_todo: seq=None 清空, seq=N 删某条后重排

per-session 串行 (LoomPool 每个 sid 一个子进程), 不考虑并发 race.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from pentaloom.models.session import SessionTodos

TodoStatus = Literal["pending", "in_progress", "completed"]

VALID_STATUS = {"pending", "in_progress", "completed"}


def _resequence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """重排 seq 字段 1..N, 保留 content/activeForm/status."""
    return [
        {
            "seq": i + 1,
            "content": str(item["content"]),
            "activeForm": str(item["activeForm"]),
            "status": item.get("status", "pending"),
        }
        for i, item in enumerate(items)
    ]


async def _load_or_create(db: AsyncSession, session_id: str) -> SessionTodos:
    row = await db.scalar(
        select(SessionTodos).where(SessionTodos.session_id == session_id)
    )
    if row is None:
        row = SessionTodos(session_id=session_id, todos_json="[]")
        db.add(row)
        await db.flush()
    return row


async def write_todos(
    db: AsyncSession, *, session_id: str, todos: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """整体覆盖. 每项必须有 content + activeForm, status 可选 (默认 pending)."""
    for t in todos:
        if "content" not in t or "activeForm" not in t:
            raise ValueError("每条 todo 必须含 content 和 activeForm 字段")
        status = t.get("status", "pending")
        if status not in VALID_STATUS:
            raise ValueError(f"status 必须是 {VALID_STATUS} 之一, 得到 {status!r}")
    items = _resequence(todos)
    row = await _load_or_create(db, session_id)
    row.todos_json = json.dumps(items, ensure_ascii=False)
    row.updated_at = func.now()
    await db.commit()
    return items


async def read_todos(
    db: AsyncSession, *, session_id: str
) -> list[dict[str, Any]]:
    row = await db.scalar(
        select(SessionTodos).where(SessionTodos.session_id == session_id)
    )
    if row is None:
        return []
    try:
        return json.loads(row.todos_json)
    except json.JSONDecodeError:
        return []


async def update_todo(
    db: AsyncSession,
    *,
    session_id: str,
    seq: int,
    status: TodoStatus | None = None,
    content: str | None = None,
    active_form: str | None = None,
) -> list[dict[str, Any]]:
    """改第 seq 项. 找不到 seq 抛 ValueError."""
    if status is not None and status not in VALID_STATUS:
        raise ValueError(f"status 必须是 {VALID_STATUS} 之一")
    items = await read_todos(db, session_id=session_id)
    target = next((t for t in items if t.get("seq") == seq), None)
    if target is None:
        raise ValueError(f"seq={seq} 不存在 (当前共 {len(items)} 条)")
    if status is not None:
        target["status"] = status
    if content is not None:
        target["content"] = content
    if active_form is not None:
        target["activeForm"] = active_form
    row = await _load_or_create(db, session_id)
    row.todos_json = json.dumps(items, ensure_ascii=False)
    row.updated_at = func.now()
    await db.commit()
    return items


async def delete_todo(
    db: AsyncSession, *, session_id: str, seq: int | None = None
) -> list[dict[str, Any]]:
    """seq=None 清空; seq=N 删某条后重排 1..N-1."""
    if seq is None:
        items: list[dict[str, Any]] = []
    else:
        cur = await read_todos(db, session_id=session_id)
        if not any(t.get("seq") == seq for t in cur):
            raise ValueError(f"seq={seq} 不存在 (当前共 {len(cur)} 条)")
        items = _resequence([t for t in cur if t.get("seq") != seq])
    row = await _load_or_create(db, session_id)
    row.todos_json = json.dumps(items, ensure_ascii=False)
    row.updated_at = func.now()
    await db.commit()
    return items


async def get_updated_at(
    db: AsyncSession, *, session_id: str
) -> str | None:
    row = await db.scalar(
        select(SessionTodos).where(SessionTodos.session_id == session_id)
    )
    if row is None or row.updated_at is None:
        return None
    return row.updated_at.isoformat()
