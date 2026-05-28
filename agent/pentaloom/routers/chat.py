"""POST /chat — SSE 流式聊天 (per-session buffer + 可重连).

设计:
  - POST /chat: body {prompt, session_id?, mounted_dirs?}
    - 没传 session_id → 新建 ChatSession
    - 传了 → 校验存在 + 校验 mounted_dirs 一致
    - 起后台 task 跑 pl.query(prompt), 每帧 append 到 per-session StreamBuffer
    - response 直接吐 buffer.stream_all() — 第一次进来就是首连, 后续任何
      GET /chat/{sid}/resume 都能重放 + 续订阅
    - 客户端 abort (刷新 / 切 session) 只关订阅, 不杀后台 task — turn 继续跑

  - GET /chat/{sid}/resume?subscribe_only=true|false:
    - false (默认): 回放 buffer 全集 + 续订阅 (适合"我什么历史都没有, 给我全部")
    - true: 只订阅新增 + 注入当前 pending (适合"我从 DB 拉过 JSONL 历史了, 别重复")
    - 没活跃 buffer 返 204

  - HITL pending: tool_use 帧 append 时同步 mark_pending; tool_result / error /
    stream_end 时 clear_pending. PERMISSION_REGISTRY.future 本来就是 in-memory,
    任何 HTTP POST /chat/permission 都能 resolve — 多连接共享免改协议.

  - POST /chat/permission/{tool_use_id}: 沿用.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from loguru import logger
from pydantic import BaseModel

from pentaloom.crud import chat_session as crud_chat
from pentaloom.infra.db import AsyncSessionLocal
from pentaloom.infra.stream_buffer import stream_buffers
from pentaloom.tools import (
    ALLOW_SESSION_TOOLS,
    HITL_TOOL_NAMES,
    PERMISSION_REGISTRY,
    REQUEST_WORKSPACE_DIR_TOOL_NAME,
    allowlist_key,
)

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}

# 挂载列表软上限. 超了 SDK 子进程启动慢 + 系统 prompt 膨胀, 不如让用户拆 session.
MAX_MOUNTED_DIRS = 10


class ChatRequest(BaseModel):
    prompt: str
    session_id: str | None = None
    # 新 session: 在这里指定要挂载哪些目录 (用户授权过的, 走 SDK add_dirs)
    # 续 session: 不传 (用 db 里的); 如果传了, 必须跟 db 一致否则 400
    mounted_dirs: list[str] | None = None


def _validate_mounted_dirs(dirs: list[str]) -> list[str]:
    """校验 + 标准化挂载目录列表. 绝对路径 + 存在 + 是 dir + 总数 <= 上限."""
    if len(dirs) > MAX_MOUNTED_DIRS:
        raise HTTPException(
            400,
            f"too many mounted_dirs ({len(dirs)} > {MAX_MOUNTED_DIRS}), "
            "split into multiple sessions",
        )
    normalized: list[str] = []
    for d in dirs:
        p = Path(d)
        if not p.is_absolute():
            raise HTTPException(400, f"mounted_dirs must be absolute: {d!r}")
        if not p.exists():
            raise HTTPException(400, f"mounted_dirs path does not exist: {d!r}")
        if not p.is_dir():
            raise HTTPException(400, f"mounted_dirs path is not a directory: {d!r}")
        normalized.append(str(p.resolve()))
    seen: set[str] = set()
    out: list[str] = []
    for d in normalized:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _serialize(msg: Any, current_msg_id: str | None = None) -> list[dict]:
    """SDK message → 前端友好的 dict 列表.

    msg_uuid 统一用 **Anthropic message.id** (msg_xxxx) 作 stable key — 横跨
    StreamEvent.content_block_delta / AssistantMessage / 历史 SessionMessage
    三套来源都对得齐, 前端 reducer 按 (msg_uuid, index) 幂等 merge + 跨源去重.

    为什么不能用 SDK 的 envelope uuid: StreamEvent.uuid 是每个 event 自己的 id
    (CLI 给的), 一个 message 的 N 个 content_block_delta 各有各的 uuid, 拿来
    当合并 key 会把 thinking 切成 N 块. AssistantMessage.uuid 跟 StreamEvent.uuid
    也不属同源, 撞不上.

    current_msg_id 由 caller (_run_query_and_fill_buffer) 通过 sniff
    StreamEvent(message_start) 维护, 见那里.
    """
    if isinstance(msg, StreamEvent):
        ev = msg.event or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta") or {}
            dt = delta.get("type")
            if dt == "text_delta":
                t = delta.get("text") or ""
                if not t:
                    return []
                return [{
                    "type": "text_delta",
                    "msg_uuid": current_msg_id,
                    "index": ev.get("index", 0),
                    "text": t,
                }]
            if dt == "thinking_delta":
                t = delta.get("thinking") or ""
                if not t:
                    return []
                return [{
                    "type": "thinking_delta",
                    "msg_uuid": current_msg_id,
                    "index": ev.get("index", 0),
                    "text": t,
                }]
        return []

    if isinstance(msg, AssistantMessage):
        # 用 msg.message_id (anthropic msg_xxxx), 跟同 turn 的 content_block_delta
        # 帧 + 历史 SessionMessage.message.id 同源, reducer 能正确合并去重.
        out = []
        for i, b in enumerate(msg.content or []):
            if isinstance(b, ToolUseBlock):
                out.append({
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.input,
                    "msg_uuid": msg.message_id,
                    "index": i,
                })
        return out

    if isinstance(msg, UserMessage):
        # user role 的 message 在 anthropic 没有 message.id (client-side 构造),
        # tool_result 反正按 tool_use_id 去重, msg_uuid 留 None 也无碍.
        out = []
        content = msg.content
        if isinstance(content, list):
            for i, b in enumerate(content):
                if isinstance(b, ToolResultBlock):
                    out.append({
                        "type": "tool_result",
                        "tool_use_id": b.tool_use_id,
                        "content": b.content,
                        "is_error": bool(b.is_error),
                        "msg_uuid": None,
                        "index": i,
                    })
        return out

    if isinstance(msg, TaskStartedMessage):
        return [{
            "type": "task_started",
            "task_id": msg.task_id,
            "subagent": (msg.data or {}).get("subagent_type"),
            "description": msg.description,
        }]

    if isinstance(msg, TaskProgressMessage):
        return [{
            "type": "task_progress",
            "task_id": msg.task_id,
            "description": msg.description,
            "last_tool": msg.last_tool_name,
        }]

    if isinstance(msg, TaskNotificationMessage):
        return [{
            "type": "task_done",
            "task_id": msg.task_id,
            "status": msg.status,
            "summary": msg.summary,
        }]

    if isinstance(msg, ResultMessage):
        return [{
            "type": "result",
            "text": msg.result,
            "is_error": msg.is_error,
            "duration_ms": msg.duration_ms,
            "cost_usd": msg.total_cost_usd,
            "num_turns": msg.num_turns,
        }]

    return []


async def _resolve_session(req: ChatRequest) -> tuple[str, list[str]]:
    """新建或读取 ChatSession, 返回 (sid, mounted_dirs)."""
    async with AsyncSessionLocal() as db:
        if req.session_id is None:
            mounted = _validate_mounted_dirs(req.mounted_dirs or [])
            sid = str(uuid.uuid4())
            title = req.prompt.strip().splitlines()[0][:60] if req.prompt.strip() else None
            await crud_chat.create_chat_session(
                db, session_id=sid, mounted_dirs=mounted, title=title,
            )
            logger.info(f"chat session created sid={sid} mounts={mounted} title={title!r}")
            return sid, mounted

        sid = req.session_id
        row = await crud_chat.get_chat_session(db, sid)
        if row is None:
            raise HTTPException(404, f"session {sid!r} not found")
        db_mounted = list(row.mounted_dirs)
        if req.mounted_dirs is not None:
            req_mounted = _validate_mounted_dirs(req.mounted_dirs)
            if sorted(req_mounted) != sorted(db_mounted):
                raise HTTPException(
                    400,
                    "mounted_dirs in request differs from session record; "
                    "to change mounts, the agent must call request_workspace_dir",
                )
        await crud_chat.touch_last_active(db, sid)
        return sid, db_mounted


async def _run_query_and_fill_buffer(pl, prompt: str, sid: str, lock: asyncio.Lock) -> None:
    """后台 task: 跑一轮 pl.query(prompt), 把每帧 append 到 stream_buffer.

    锁: per-session lock 串行化 turn (LoomPool 给的); 同 sid 同时只跑一轮.
    异常: query 抛错 emit error frame; 一定走 finally append stream_end + finish().

    msg_id 追踪: anthropic stream 协议里 message_start 携带 message.id, 后续同
    一 message 的 content_block_delta 没自带 message id. 我们在这里维护
    current_msg_id, 传给 _serialize 让 thinking_delta / text_delta 帧都带上
    稳定的 (msg_uuid=message.id, index) — 前端 reducer 才能把同 message 的所有
    delta 合并成一个 thinking / text 块, 不会切碎. message_stop 后清空.
    """
    buf = stream_buffers.get(sid)
    assert buf is not None, f"stream_buffer should exist for {sid}"
    current_msg_id: str | None = None
    async with lock:
        try:
            async for msg in pl.query(prompt):
                if isinstance(msg, StreamEvent):
                    ev = msg.event or {}
                    et = ev.get("type")
                    if et == "message_start":
                        current_msg_id = (ev.get("message") or {}).get("id")
                    elif et == "message_stop":
                        current_msg_id = None
                for frame in _serialize(msg, current_msg_id):
                    chunk = _sse(frame)
                    buf.append(chunk)
                    # 跟踪 HITL pending: tool_use(HITL 工具) → mark; tool_result → clear
                    if frame.get("type") == "tool_use" and frame.get("name") in HITL_TOOL_NAMES:
                        buf.mark_pending(str(frame.get("id", "")), chunk)
                    elif frame.get("type") == "tool_result":
                        buf.clear_pending(str(frame.get("tool_use_id", "")))
        except asyncio.CancelledError:
            logger.info(f"chat task cancelled (session={sid})")
            buf.append(_sse({"type": "error", "message": "cancelled"}))
            raise
        except Exception as e:
            logger.exception(f"chat task failed (session={sid}): {e}")
            buf.append(_sse({"type": "error", "message": str(e)}))
        finally:
            buf.append(_sse({"type": "stream_end"}))
            buf.finish()


@router.post("/chat", summary="向 PentaLoom 发一条消息, SSE 流回 (支持后续 GET /resume 重连)")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(500, "LoomPool not initialized")

    sid, mounted = await _resolve_session(req)

    try:
        pl, lock = await pool.get(sid, mounted)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # 占住 buffer 名额 + 起后台 task. 后台 task 拿到 lock 才会真正开跑 (同 sid 上
    # 轮没结束就排队), buffer 在前的 chunks 会保留, 重连仍能拿到.
    buf = stream_buffers.create_for_turn(sid)
    buf.set_user_prompt(req.prompt)
    task = asyncio.create_task(_run_query_and_fill_buffer(pl, req.prompt, sid, lock))
    buf.set_task(task)

    return StreamingResponse(
        buf.stream_all(),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Session-Id": sid},
    )


@router.get(
    "/chat/{sid}/resume",
    summary="重连 / 订阅当前 turn 的 SSE 流",
)
async def resume(sid: str) -> Response:
    """重连接当前 turn.

    - 没活跃 buffer (没跑过 / 被 evict) → 204
    - buffer 存在但 status=COMPLETE (上一轮跑完, 没新 turn) → 204
      理由: turn 结束的 frames 已落 JSONL, /messages 能拿全; 这里若重放只会
      跟历史重复. 前端不该再处理.
    - 否则: stream_all 重放 (delta 折叠) + 续订阅.
      前端按 (msg_uuid, index) 跨源去重 + 幂等 merge, 不会出双份. 不再有
      subscribe_only 分支 — 全部走唯一一条路径, 重放安全.
    """
    from pentaloom.infra.stream_buffer import StreamStatus

    buf = stream_buffers.get(sid)
    if buf is None or buf.status == StreamStatus.COMPLETE:
        return Response(status_code=204)

    # 重连先注入 user_prompt frame, 让前端把"我刚发的那条"补上 — 用户原 prompt
    # 既不进 buffer.chunks (UserMessage 文本走 _serialize 时被跳过), 也未必在
    # SWR 拉到的 JSONL 里 (SDK 写时机不保证). 不注入的话刷新 / 切走再回 / 多 tab
    # 都会看到自己消息消失到 turn 结束才出现.
    user_prompt = buf.user_prompt
    stream = buf.stream_all()

    async def _with_user_prompt() -> AsyncIterator[str]:
        if user_prompt:
            yield _sse({"type": "user_prompt", "text": user_prompt})
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        _with_user_prompt(),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Session-Id": sid},
    )


class PermissionDecision(BaseModel):
    """前端针对一次 HITL 工具调用的回执."""

    session_id: str
    decision: Literal["allow_once", "allow_session", "deny"]


@router.post(
    "/chat/permission/{tool_use_id}",
    summary="回复一次 HITL 工具审批",
)
async def chat_permission(
    tool_use_id: str, body: PermissionDecision, request: Request
) -> dict:
    pending = PERMISSION_REGISTRY.peek(body.session_id, tool_use_id)
    if pending is None:
        raise HTTPException(
            404, f"no pending permission for tool_use_id={tool_use_id!r}"
        )

    allow = body.decision != "deny"
    added_path: str | None = None
    added_allowlist_key: str | None = None

    if allow and pending.tool_name == REQUEST_WORKSPACE_DIR_TOOL_NAME:
        path = str(pending.tool_input.get("path", "")).strip()
        try:
            async with AsyncSessionLocal() as db:
                await crud_chat.add_mounted_dir(
                    db, session_id=body.session_id, path=path
                )
            added_path = path
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

    if (
        allow
        and body.decision == "allow_session"
        and pending.tool_name in ALLOW_SESSION_TOOLS
    ):
        key = allowlist_key(pending.tool_name, pending.tool_input)
        if key:
            pool = getattr(request.app.state, "pool", None)
            if pool is not None and pool.add_hitl_allowed(
                body.session_id, pending.tool_name, key
            ):
                added_allowlist_key = key

    PERMISSION_REGISTRY.resolve(body.session_id, tool_use_id, allow=allow)
    # 同步 buffer pending 状态 (deny 时后端不会再 emit tool_result, 但 _serialize
    # 里 deny 走 UserMessage 的 tool_result is_error=True, 会自然 clear. 这里
    # 提前清一下也无害, 防止 buffer 状态滞后).
    buf = stream_buffers.get(body.session_id)
    if buf is not None:
        buf.clear_pending(tool_use_id)
        # 推一帧 permission_resolved, 让所有订阅者 (含刷新后重连) 知道审批已落定.
        # 不依赖 tool_result 来 dismiss 审批栏 — 工具实际执行可能要几分钟 (uv add
        # browser-use 等), 期间审批栏要先消失, ToolRow 回退到 in-progress 状态.
        buf.append(_sse({
            "type": "permission_resolved",
            "tool_use_id": tool_use_id,
            "decision": body.decision,
        }))
    logger.info(
        f"permission resolved sid={body.session_id} tool_use_id={tool_use_id} "
        f"tool={pending.tool_name} decision={body.decision} "
        f"added_path={added_path} added_allowlist_key={added_allowlist_key!r}"
    )
    return {
        "session_id": body.session_id,
        "tool_use_id": tool_use_id,
        "decision": body.decision,
        "added_path": added_path,
        "added_allowlist_key": added_allowlist_key,
    }
