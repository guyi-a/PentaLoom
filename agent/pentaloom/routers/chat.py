"""POST /chat — SSE 流式聊天.

设计:
  - body: {"prompt": "...", "session_id": "..."?, "mounted_dirs": [...]?}
  - response: text/event-stream, 每帧 data: {json}\n\n
    response header X-Session-Id 回写当前 session_id (前端首次拿到, 后续带上)
  - 没传 session_id → 新建 ChatSession (mounted_dirs 校验后入 db, 沙箱目录自动建)
  - 传了 session_id → db 拿 mounted_dirs, 请求里的 mounted_dirs 必须一致或不传 (防呆)
  - LoomPool 给每个 session 一个常驻 PentaLoom (= 独立 CLI 子进程),
    搭配 per-session asyncio.Lock 串行化该 session 内的 turn —
    不同 session 之间不互阻.
"""

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
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from pentaloom.crud import chat_session as crud_chat
from pentaloom.infra.db import AsyncSessionLocal
from pentaloom.tools import (
    BASH_TOOL_NAME,
    PERMISSION_REGISTRY,
    REQUEST_WORKSPACE_DIR_TOOL_NAME,
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
    # 去重保留顺序
    seen: set[str] = set()
    out: list[str] = []
    for d in normalized:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _serialize(msg: Any) -> list[dict]:
    """SDK message → 前端友好的 dict 列表 (一条 message 可能拆多帧).

    注意: TaskStartedMessage / TaskProgressMessage / TaskNotificationMessage 都是
    SystemMessage 子类, 所以 Task* 分支必须放在 SystemMessage 兜底之前, 否则会
    被 isinstance(msg, SystemMessage) 吞掉返回 [].

    流式: 因为 options 开了 include_partial_messages=True, 文本由 StreamEvent
    分支以 text_delta 帧逐段推出. AssistantMessage 里的 TextBlock 跳过, 避免
    跟 delta 重复 (delta 累积出的就是最终文本); tool_use / thinking 仍然按完整
    block emit.
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
                    "msg_uuid": msg.uuid,
                    "index": ev.get("index", 0),
                    "text": t,
                }]
            if dt == "thinking_delta":
                t = delta.get("thinking") or ""
                if not t:
                    return []
                return [{
                    "type": "thinking_delta",
                    "msg_uuid": msg.uuid,
                    "index": ev.get("index", 0),
                    "text": t,
                }]
        return []

    if isinstance(msg, AssistantMessage):
        # text / thinking 走 StreamEvent 流式; 这里只挑 tool_use (input 已 parse 好)
        return [
            {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            for b in (msg.content or [])
            if isinstance(b, ToolUseBlock)
        ]

    if isinstance(msg, UserMessage):
        out = []
        content = msg.content
        if isinstance(content, list):
            for b in content:
                if isinstance(b, ToolResultBlock):
                    out.append({
                        "type": "tool_result",
                        "tool_use_id": b.tool_use_id,
                        "content": b.content,
                        "is_error": bool(b.is_error),
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
    """新建或读取 ChatSession, 返回 (sid, mounted_dirs).

    新 session: 校验 mounted_dirs, 生成 sid, 入 db.
    续 session: 拒绝不存在的 sid; 请求带了 mounted_dirs 必须跟 db 一致 (防呆).
    """
    async with AsyncSessionLocal() as db:
        if req.session_id is None:
            mounted = _validate_mounted_dirs(req.mounted_dirs or [])
            sid = str(uuid.uuid4())
            await crud_chat.create_chat_session(
                db, session_id=sid, mounted_dirs=mounted
            )
            logger.info(f"chat session created sid={sid} mounts={mounted}")
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


@router.post("/chat", summary="向 PentaLoom 发一条消息, SSE 流回")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(500, "LoomPool not initialized")

    sid, mounted = await _resolve_session(req)

    try:
        pl, lock = await pool.get(sid, mounted)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    async def generate() -> AsyncIterator[str]:
        async with lock:
            try:
                async for msg in pl.query(req.prompt):
                    for frame in _serialize(msg):
                        yield _sse(frame)
            except Exception as e:
                logger.exception(f"chat stream failed (session={sid}): {e}")
                yield _sse({"type": "error", "message": str(e)})
            finally:
                yield _sse({"type": "stream_end"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Session-Id": sid},
    )


class PermissionDecision(BaseModel):
    """前端针对一次 HITL 工具调用的回执.

    decision 语义:
      - allow_once    : 仅放行当前这次调用.
      - allow_session : 放行 + 加进本会话白名单 (仅 Bash 有效, 同一条 cmd 下次免审;
                        workspace 一次性, allow_session 等价于 allow_once).
      - deny          : 拒绝.
    """

    session_id: str
    decision: Literal["allow_once", "allow_session", "deny"]


@router.post(
    "/chat/permission/{tool_use_id}",
    summary="回复一次 HITL 工具审批 (Bash / request_workspace_dir)",
)
async def chat_permission(
    tool_use_id: str, body: PermissionDecision, request: Request
) -> dict:
    """前端在收到 tool_use 帧并让用户选择后, POST 到这里. 后端:
      1. 根据 pending.tool_name 走不同的 side-effect (workspace 写 db,
         bash 加 session allowlist), 再 resolve Future.
      2. workspace 的 mount 不在这里 rebuild LoomPool — 由 LoomPool.get 下一轮
         turn 时检测 db 里 mounted_dirs 跟 entry.mounted_dirs 不一致自动 rebuild
         (走 resume 接回上下文).
    """
    pending = PERMISSION_REGISTRY.peek(body.session_id, tool_use_id)
    if pending is None:
        raise HTTPException(
            404, f"no pending permission for tool_use_id={tool_use_id!r}"
        )

    allow = body.decision != "deny"
    added_path: str | None = None
    added_bash_cmd: str | None = None

    if allow and pending.tool_name == REQUEST_WORKSPACE_DIR_TOOL_NAME:
        path = str(pending.tool_input.get("path", "")).strip()
        try:
            async with AsyncSessionLocal() as db:
                await crud_chat.add_mounted_dir(
                    db, session_id=body.session_id, path=path
                )
            added_path = path
        except ValueError as e:
            # session 不在 db (理论上不可能, 因为 pending 是从 chat turn 里产生的)
            raise HTTPException(404, str(e)) from e

    if (
        allow
        and body.decision == "allow_session"
        and pending.tool_name == BASH_TOOL_NAME
    ):
        cmd = str(pending.tool_input.get("command", "")).strip()
        if cmd:
            pool = getattr(request.app.state, "pool", None)
            # pool 缺失 (initialized 前) / sid evict 都视为 best-effort 失败 — 不阻塞
            # 当前这次 allow, 只是失去"下次免审"红利. 用户可以再点一次.
            if pool is not None and pool.add_bash_allowed(body.session_id, cmd):
                added_bash_cmd = cmd

    PERMISSION_REGISTRY.resolve(body.session_id, tool_use_id, allow=allow)
    logger.info(
        f"permission resolved sid={body.session_id} tool_use_id={tool_use_id} "
        f"tool={pending.tool_name} decision={body.decision} "
        f"added_path={added_path} added_bash_cmd={added_bash_cmd!r}"
    )
    return {
        "session_id": body.session_id,
        "tool_use_id": tool_use_id,
        "decision": body.decision,
        "added_path": added_path,
        "added_bash_cmd": added_bash_cmd,
    }
