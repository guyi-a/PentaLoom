"""Session 管理接口 (列表 / meta / 历史 / 删除 / 改 title).

跟 /chat 拆开, 因为这些是无 LLM 的纯 CRUD + 历史读取, 不走 LoomPool.

历史接口的设计:
  - 走 SDK helper `get_session_messages_from_store(store, sid, directory=sandbox)`
  - SessionMessage.message 是 raw Anthropic API dict (role + content blocks)
  - 在这里转成跟 /chat SSE 帧一致的 dict 列表, 前端只需一套 renderer
  - 历史里只有 user/assistant, 不会有 task_started/result 那些 (运行时 emit, 不入 JSONL)
"""

import shutil
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    SessionMessage,
    get_session_messages_from_store,
)
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from pentaloom.config import get_settings
from pentaloom.crud import chat_session as crud_chat
from pentaloom.infra import SQLiteSessionStore
from pentaloom.infra.db import AsyncSessionLocal
from pentaloom.models.session import (
    ChatSession,
    SessionEntry,
    SessionMtime,
    SessionSummary,
)
from sqlalchemy import delete

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionMeta(BaseModel):
    """单 session 元数据 — 前端列表 / 详情都用这个."""

    session_id: str
    title: str | None
    mounted_dirs: list[str]
    created_at: str
    last_active_at: str

    @classmethod
    def from_row(cls, row: ChatSession) -> "SessionMeta":
        return cls(
            session_id=row.session_id,
            title=row.title,
            mounted_dirs=list(row.mounted_dirs),
            created_at=row.created_at.isoformat(),
            last_active_at=row.last_active_at.isoformat(),
        )


class SessionPatch(BaseModel):
    title: str | None = None


def _content_blocks_to_frames(content: Any) -> list[dict]:
    """Anthropic API content (str | list[block]) → 跟 /chat SSE 一致的 frame 列表."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            out.append({"type": "text", "text": b.get("text", "")})
        elif t == "thinking":
            out.append({"type": "thinking", "text": b.get("thinking", "")})
        elif t == "tool_use":
            out.append({
                "type": "tool_use",
                "id": b.get("id"),
                "name": b.get("name"),
                "input": b.get("input"),
            })
        elif t == "tool_result":
            out.append({
                "type": "tool_result",
                "tool_use_id": b.get("tool_use_id"),
                "content": b.get("content"),
                "is_error": bool(b.get("is_error", False)),
            })
        # 其他 type 暂忽略 (image / document 之类后面再说)
    return out


def _session_message_to_frames(sm: SessionMessage) -> list[dict]:
    """SessionMessage → frame 列表. role=user 的 tool_result 也会被解出来."""
    msg = sm.message or {}
    if not isinstance(msg, dict):
        return []
    return _content_blocks_to_frames(msg.get("content"))


@router.get("", summary="所有 ChatSession (按最近活跃倒序)")
async def list_sessions() -> list[SessionMeta]:
    async with AsyncSessionLocal() as db:
        rows = await crud_chat.list_chat_sessions(db)
    return [SessionMeta.from_row(r) for r in rows]


@router.get("/{sid}", summary="单个 ChatSession 元数据")
async def get_session(sid: str) -> SessionMeta:
    async with AsyncSessionLocal() as db:
        row = await crud_chat.get_chat_session(db, sid)
    if row is None:
        raise HTTPException(404, f"session {sid!r} not found")
    return SessionMeta.from_row(row)


@router.get("/{sid}/messages", summary="session 完整对话历史 (按时间序)")
async def get_session_messages(
    sid: str, limit: int | None = None, offset: int = 0
) -> list[dict]:
    """每条 SessionMessage 拍平成跟 /chat SSE 同结构的 frame 序列.

    返回结构: [{role: 'user'|'assistant', uuid: str, frames: [...]}].
    frames 里跟 /chat 的 {type: text/tool_use/tool_result/thinking/...} 一致.
    """
    settings = get_settings()
    sandbox = settings.sandbox_dir_for(sid)
    if not sandbox.exists():
        # 沙箱不存在说明 session 从没跑过 (db 行可能也没建), 直接 404
        raise HTTPException(404, f"session {sid!r} has no transcript")

    store = SQLiteSessionStore()
    try:
        msgs = await get_session_messages_from_store(
            store, sid, directory=str(sandbox), limit=limit, offset=offset
        )
    except Exception as e:
        logger.exception(f"failed to read history for {sid}: {e}")
        raise HTTPException(500, str(e)) from e

    return [
        {
            "role": m.type,
            "uuid": m.uuid,
            "frames": _session_message_to_frames(m),
        }
        for m in msgs
    ]


@router.patch("/{sid}", summary="改 session 元数据 (目前只支持 title)")
async def patch_session(sid: str, patch: SessionPatch) -> SessionMeta:
    async with AsyncSessionLocal() as db:
        row = await crud_chat.get_chat_session(db, sid)
        if row is None:
            raise HTTPException(404, f"session {sid!r} not found")
        if patch.title is not None:
            row.title = patch.title
            await db.commit()
            await db.refresh(row)
        return SessionMeta.from_row(row)


@router.delete("/{sid}", summary="永久删除 session: db 行 + 沙箱 + 镜像表 + LoomPool 缓存")
async def delete_session(sid: str, request: Request) -> dict:
    """SDK JSONL 不删 — SDK 没暴露 helper, 自己定位路径太脆 (graceful degradation).
    后续 SDK 加 delete_session 接口再补上, 也可以加个 cron 自己扫.
    """
    settings = get_settings()
    if not Path(sid).name:  # 防御性, 不让走出去
        raise HTTPException(400, "invalid sid")

    deleted: dict[str, bool] = {}

    # 1. 先 evict LoomPool 缓存的 client (否则它仍持有 SDK session, 续聊会复活)
    pool = getattr(request.app.state, "pool", None)
    if pool is not None:
        try:
            await pool.evict(sid)
            deleted["pool"] = True
        except Exception:
            logger.exception(f"pool evict failed for {sid}")
            deleted["pool"] = False

    # 2. db: chat_sessions + 三张镜像表
    async with AsyncSessionLocal() as db:
        row = await crud_chat.get_chat_session(db, sid)
        if row is None:
            raise HTTPException(404, f"session {sid!r} not found")
        await db.execute(delete(SessionEntry).where(SessionEntry.session_id == sid))
        await db.execute(delete(SessionMtime).where(SessionMtime.session_id == sid))
        await db.execute(delete(SessionSummary).where(SessionSummary.session_id == sid))
        await db.delete(row)
        await db.commit()
        deleted["db"] = True

    # 3. 沙箱目录
    sandbox = settings.sandbox_dir_for(sid)
    if sandbox.exists():
        shutil.rmtree(sandbox, ignore_errors=True)
        deleted["sandbox"] = True
    else:
        deleted["sandbox"] = False

    logger.info(f"session {sid} deleted: {deleted}")
    return {"session_id": sid, "deleted": deleted}
