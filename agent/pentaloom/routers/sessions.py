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
)
from claude_agent_sdk._internal.sessions import project_key_for_directory
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from pentaloom.config import get_settings
from pentaloom.crud import chat_session as crud_chat
from pentaloom.crud import todos as todos_crud
from pentaloom.infra import SQLiteSessionStore
from pentaloom.infra.db import AsyncSessionLocal
from pentaloom.infra.prompt_blocks import strip_internal_prompt_blocks
from pentaloom.infra.session_status import session_status
from pentaloom.infra.stream_buffer import stream_buffers
from pentaloom.models.session import (
    ChatSession,
    SessionEntry,
    SessionMtime,
    SessionSummary,
    SessionTodos,
)
from pentaloom.routers.chat import _validate_mounted_dirs
from sqlalchemy import delete

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionMeta(BaseModel):
    """单 session 元数据 — 前端列表 / 详情都用这个."""

    session_id: str
    title: str | None
    mounted_dirs: list[str]
    sandbox_dir: str  # agent 默认 cwd; 不持久化, 从 settings 算出来
    created_at: str
    last_active_at: str

    @classmethod
    def from_row(cls, row: ChatSession) -> "SessionMeta":
        # SQLite `func.now()` 写的是 UTC naive datetime, isoformat 不带时区 ->
        # JS new Date(iso) 会当成本地时间解析, 北京时区下差 8h. 补 "Z" 显式标 UTC.
        settings = get_settings()
        return cls(
            session_id=row.session_id,
            title=row.title,
            mounted_dirs=list(row.mounted_dirs),
            sandbox_dir=str(settings.sandbox_dir_for(row.session_id)),
            created_at=row.created_at.isoformat() + "Z",
            last_active_at=row.last_active_at.isoformat() + "Z",
        )


class SessionPatch(BaseModel):
    title: str | None = None


class MountsPatch(BaseModel):
    """整体替换或增删 mounted_dirs.

    - 提供 dirs: 整体替换 (UI 上"重排/清空"用)
    - 提供 add/remove: 增量改 (UI 上 [+] 加挂载, hover-remove 删一条)
    - 三者互斥, 同时给 add/remove 可以一次性 batch
    """

    dirs: list[str] | None = None
    add: list[str] | None = None
    remove: list[str] | None = None


def _content_blocks_to_frames(content: Any, msg_uuid: str | None) -> list[dict]:
    """Anthropic API content (str | list[block]) → 跟 /chat SSE 一致的 frame 列表.

    给每个 frame 带上 (msg_uuid, index) — 跟 streaming 端的 text_delta /
    thinking_delta / tool_use / tool_result 一一对齐, 方便前端 ChatStream
    跨源去重 (历史 ∩ liveFrames) + reducer 幂等 merge.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content, "msg_uuid": msg_uuid, "index": 0}]
    if not isinstance(content, list):
        return []
    out: list[dict] = []
    for i, b in enumerate(content):
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            out.append({
                "type": "text",
                "text": b.get("text", ""),
                "msg_uuid": msg_uuid,
                "index": i,
            })
        elif t == "thinking":
            out.append({
                "type": "thinking",
                "text": b.get("thinking", ""),
                "msg_uuid": msg_uuid,
                "index": i,
            })
        elif t == "tool_use":
            out.append({
                "type": "tool_use",
                "id": b.get("id"),
                "name": b.get("name"),
                "input": b.get("input"),
                "msg_uuid": msg_uuid,
                "index": i,
            })
        elif t == "tool_result":
            out.append({
                "type": "tool_result",
                "tool_use_id": b.get("tool_use_id"),
                "content": b.get("content"),
                "is_error": bool(b.get("is_error", False)),
                "msg_uuid": msg_uuid,
                "index": i,
            })
        # 其他 type 暂忽略 (image / document 之类后面再说)
    return out


def _session_message_to_frames(
    sm: SessionMessage,
) -> tuple[str | None, list[dict], int, list[dict]]:
    """SessionMessage → (anthropic message.id, frames, attachment_count, inline_images).

    msg_uuid 用 anthropic message.id (msg_xxxx), 跟 /chat SSE 端的 thinking/text
    delta + AssistantMessage tool_use 同源 — 前端跨源去重 + 幂等 merge 才对得齐.
    user role 没 message.id (client 端构造), 返 None.

    user role 出口处理两件事:
      1. text content 走 strip_internal_prompt_blocks 剥 <pentaloom_internal_attachments>
         块, 数附件个数 → attachment_count (落盘附件).
      2. 收集 list 里 image type block 的 base64 → 转 data URL 灌进 inline_images.
         _content_blocks_to_frames 不输出 image type frame (前端不需要 image 进
         turn 状态机), data URL 直接挂 entry top-level 给 UserBubble 渲缩略图.
    """
    msg = sm.message or {}
    if not isinstance(msg, dict):
        return None, [], 0, []
    message_id = msg.get("id") if isinstance(msg.get("id"), str) else None
    content = msg.get("content")
    attachment_count = 0
    inline_images: list[dict] = []

    if sm.type == "user":
        # user content 可能是 str 或 list[block]; 两种都要 strip text 部分.
        if isinstance(content, str):
            stripped, count = strip_internal_prompt_blocks(content)
            attachment_count += count
            content = stripped
        elif isinstance(content, list):
            new_blocks: list[Any] = []
            for b in content:
                if isinstance(b, dict):
                    btype = b.get("type")
                    if btype == "text":
                        stripped, count = strip_internal_prompt_blocks(
                            b.get("text", "")
                        )
                        attachment_count += count
                        new_blocks.append({**b, "text": stripped})
                        continue
                    if btype == "image":
                        # 拼 data URL — 前端 <img src> 直接消费, 不需要再走 endpoint.
                        # 量大问题: SDK transcript 里就是 base64, 历史拉一次几百 KB,
                        # 真有性能问题再加 thumbnail 缓存 / endpoint 拉单图.
                        src = b.get("source") or {}
                        if (
                            isinstance(src, dict)
                            and src.get("type") == "base64"
                            and isinstance(src.get("data"), str)
                            and isinstance(src.get("media_type"), str)
                        ):
                            inline_images.append({
                                "src": f"data:{src['media_type']};base64,{src['data']}",
                            })
                        # image block 不进 frame
                        continue
                new_blocks.append(b)
            content = new_blocks

    return (
        message_id,
        _content_blocks_to_frames(content, message_id),
        attachment_count,
        inline_images,
    )


SSE_STATUS_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@router.get(
    "/status/stream",
    summary="全局 SSE 长连 — 推送各 session 的状态变化 (running / waiting_approval / idle)",
)
async def stream_session_status() -> StreamingResponse:
    """sidebar 挂载时 open 一次, 卸载关. 一份连接覆盖所有 session.

    跟 /chat 的 per-turn SSE 流物理分开 (不同 endpoint, 不同 buffer):
      - /chat 流: 携带消息内容, 生命周期 = turn
      - /sessions/status/stream: 携带状态枚举, 生命周期 = sidebar 挂载期

    新订阅者订阅瞬间, publisher 先 yield 当前所有非 idle 状态 snapshot —
    用户刚开 app / 切回页面时不需要等下次 turn 才看到"哪些会话在跑".
    """

    async def gen():
        async for chunk in session_status.subscribe():
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=SSE_STATUS_HEADERS,
    )


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


@router.get("/{sid}/todos", summary="session 当前 todo 列表")
async def get_session_todos(sid: str) -> dict[str, Any]:
    """前端右栏 Todo section 拉这个 endpoint. 不校 session 存在性 (空表也合法)."""
    async with AsyncSessionLocal() as db:
        items = await todos_crud.read_todos(db, session_id=sid)
        updated_at = await todos_crud.get_updated_at(db, session_id=sid)
    return {"todos": items, "updated_at": updated_at}


def _entry_to_session_message(entry: dict) -> SessionMessage | None:
    """SDK store 里的一行 entry → SessionMessage. 不走 parentUuid 链还原.

    SDK 自带 get_session_messages_from_store 用 leaf→root 单链回溯, 并行 tool_use
    场景 (一个 assistant 同时调 2+ 工具, 树在 tool_use 处分叉) 会丢掉除 main chain
    以外的分支 — 实测见 sid 55a358dc: Bash + install_libs 并行, Bash 的 tool_result
    所在分支被裁, 前端拿不到 result → Bash chip 永远转圈.

    我们不需要 chain 重建, 按 seq 平铺给前端就够 (前端会用 message_id + tool_use_id
    跨 message 配对). 这里只做 visibility 过滤 (与 SDK 内部 _is_visible_message 一致).
    """
    etype = entry.get("type")
    if etype not in ("user", "assistant"):
        return None
    if entry.get("isMeta") or entry.get("isSidechain") or entry.get("teamName"):
        return None
    return SessionMessage(
        type="user" if etype == "user" else "assistant",  # type: ignore[arg-type]
        uuid=entry.get("uuid", ""),
        session_id=entry.get("sessionId", ""),
        message=entry.get("message"),
        parent_tool_use_id=None,
    )


async def _load_session_messages_linear(
    sid: str, directory: str, limit: int | None, offset: int
) -> list[SessionMessage]:
    """按 seq 顺序读完整 transcript, 不走 parentUuid 单链回溯 — 保留所有并行分支."""
    store = SQLiteSessionStore()
    project_key = project_key_for_directory(directory)
    entries = await store.load({"project_key": project_key, "session_id": sid})
    if not entries:
        return []
    msgs: list[SessionMessage] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        sm = _entry_to_session_message(e)
        if sm is not None:
            msgs.append(sm)
    if limit is not None and limit > 0:
        return msgs[offset : offset + limit]
    if offset > 0:
        return msgs[offset:]
    return msgs


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

    try:
        msgs = await _load_session_messages_linear(
            sid, str(sandbox), limit=limit, offset=offset
        )
    except Exception as e:
        logger.exception(f"failed to read history for {sid}: {e}")
        raise HTTPException(500, str(e)) from e

    return [
        _build_message_entry(m) for m in msgs
    ]


def _build_message_entry(m: SessionMessage) -> dict:
    message_id, frames, attachment_count, inline_images = (
        _session_message_to_frames(m)
    )
    entry: dict[str, Any] = {
        "role": m.type,
        "uuid": m.uuid,                # envelope uuid - 用作 React key
        "message_id": message_id,      # anthropic message.id - 用作跨源去重
        "frames": frames,
    }
    # 仅 user role + 真有附件 / 内嵌图片的消息才挂字段; 老消息 / assistant 都不带,
    # 前端只在见到字段时才渲染.
    if attachment_count > 0:
        entry["attachment_count"] = attachment_count
    if inline_images:
        # data URL 列表 (含 base64). 前端 user bubble 渲缩略图 grid.
        entry["inline_images"] = inline_images
    return entry


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


@router.patch("/{sid}/mounts", summary="改 session 的 mounted_dirs (会 evict LoomPool client, 下轮重建)")
async def patch_mounts(sid: str, patch: MountsPatch, request: Request) -> SessionMeta:
    """改完后 evict pool entry — 下条用户消息触发 LoomPool 重建时新 add_dirs 才生效.

    跟 request_workspace_dir 工具语义一致: 当前 turn 不受影响, 下轮才看到新挂载.
    """
    if patch.dirs is None and patch.add is None and patch.remove is None:
        raise HTTPException(400, "must supply at least one of dirs/add/remove")

    async with AsyncSessionLocal() as db:
        row = await crud_chat.get_chat_session(db, sid)
        if row is None:
            raise HTTPException(404, f"session {sid!r} not found")

        if patch.dirs is not None:
            new_dirs = list(patch.dirs)
        else:
            new_dirs = list(row.mounted_dirs)
            if patch.add:
                for d in patch.add:
                    if d not in new_dirs:
                        new_dirs.append(d)
            if patch.remove:
                remove_set = set(patch.remove)
                new_dirs = [d for d in new_dirs if d not in remove_set]

        new_dirs = _validate_mounted_dirs(new_dirs)
        await crud_chat.set_mounted_dirs(db, sid, new_dirs)
        row = await crud_chat.get_chat_session(db, sid)
        assert row is not None

    pool = getattr(request.app.state, "pool", None)
    if pool is not None:
        try:
            await pool.evict(sid)
        except Exception:
            logger.exception(f"pool evict failed after mounts patch sid={sid}")

    logger.info(f"mounts patched sid={sid} new={new_dirs}")
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

    # 1.5 兜底 buffer 清理 — pool.evict 在 entry 命中时已经清过, 但当前 session
    # 没在 pool (如从未跑过 / 已 LRU 出局) 时不会清, 这里补一刀.
    stream_buffers.remove(sid)
    session_status.remove(sid)

    # 2. db: chat_sessions + 三张镜像表
    async with AsyncSessionLocal() as db:
        row = await crud_chat.get_chat_session(db, sid)
        if row is None:
            raise HTTPException(404, f"session {sid!r} not found")
        await db.execute(delete(SessionEntry).where(SessionEntry.session_id == sid))
        await db.execute(delete(SessionMtime).where(SessionMtime.session_id == sid))
        await db.execute(delete(SessionSummary).where(SessionSummary.session_id == sid))
        await db.execute(delete(SessionTodos).where(SessionTodos.session_id == sid))
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
