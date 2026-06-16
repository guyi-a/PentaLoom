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
import base64
import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal

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
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from loguru import logger
from pydantic import BaseModel

from pentaloom.config import get_settings
from pentaloom.crud import chat_session as crud_chat
from pentaloom.infra.approval.policy import APPROVAL_MODES
from pentaloom.infra.attachments import (
    AttachmentTooLarge,
    commit_attachment,
    pick_unique_dest,
    sanitize_filename,
)
from pentaloom.infra.db import AsyncSessionLocal
from pentaloom.infra.prompt_blocks import build_attachments_block
from pentaloom.infra.session_status import session_status
from pentaloom.infra.stream_buffer import stream_buffers
from pentaloom.tools import (
    ALLOW_SESSION_TOOLS,
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
    # 首条消息时 build _Entry 直接用这个 mode. 之前是默认 default + 前端事后
    # PATCH, 但 PATCH 在 turn 已经启动后才到, 第一个工具调用走 default 弹审批.
    # None / 不传 → backend fallback "default"; 已 build 的 session 这字段 ignored
    # (mode 由 PATCH /approval-mode 改).
    approval_mode: str | None = None


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


async def _run_query_and_fill_buffer(
    pl,
    prompt: str,
    inline_image_blocks: list[dict[str, Any]] | None,
    sid: str,
    lock: asyncio.Lock,
    pool,
) -> None:
    """后台 task: 跑一轮 pl.query / pl.query_multimodal, 把每帧 append 到 stream_buffer.

    锁: per-session lock 串行化 turn (LoomPool 给的); 同 sid 同时只跑一轮.
    异常: query 抛错 emit error frame; 一定走 finally append stream_end + finish().

    inline_image_blocks: list[dict] 时走 multimodal 路径 — 把 image 块跟 text 块
    拼成 content_blocks list 喂给 SDK; None 时走 plain str query 路径 (现有行为).

    msg_id 追踪: anthropic stream 协议里 message_start 携带 message.id, 后续同
    一 message 的 content_block_delta 没自带 message id. 我们在这里维护
    current_msg_id, 传给 _serialize 让 thinking_delta / text_delta 帧都带上
    稳定的 (msg_uuid=message.id, index) — 前端 reducer 才能把同 message 的所有
    delta 合并成一个 thinking / text 块, 不会切碎. message_stop 后清空.

    weaver hot reload: 本 turn 跑过 weave_skill /
    edit_weaver / delete_weaver, _Entry.pending_rebuild 被 mark True; 在
    stream_end 之后 (确保 SSE 已优雅关闭) 调 pool.evict(sid), 下条 user
    message 触发 LoomPool.get → resume rebuild, 新 weaver 内容立即生效.
    必须**在 stream_end 之后** — 不然 SDK 子进程在 stream 中被 SIGTERM, turn 卡死.
    """
    buf = stream_buffers.get(sid)
    assert buf is not None, f"stream_buffer should exist for {sid}"
    current_msg_id: str | None = None

    if inline_image_blocks:
        # content blocks: image 在前, text 在后 (Anthropic 推荐 ordering — 让 LLM
        # 先看图再读文本指令). text 块用 internal_prompt (含 attachments block).
        content_blocks = [*inline_image_blocks, {"type": "text", "text": prompt}]
        message_iter = pl.query_multimodal(content_blocks)
    else:
        message_iter = pl.query(prompt)

    async with lock:
        try:
            async for msg in message_iter:
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
                    # tool_use → mark_pending 的静态判断已删 — 现在 mark_pending
                    # 由 workspace.py make_can_use_tool 在 REGISTRY.register Future 之
                    # 后调用, 跟 permission_request 帧同 trigger 点. 这里只保留
                    # tool_result → clear_pending 兜底 (work tools 跑完时清状态).
                    if frame.get("type") == "tool_result":
                        buf.clear_pending(str(frame.get("tool_use_id", "")))
                        session_status.set_status(
                            sid,
                            "waiting_approval" if buf.has_pending() else "running",
                        )
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
            # 状态机收尾 — turn 跑完不论成功/失败/cancel, 一律切 idle 让 sidebar
            # spinner 停转. 跟 stream_end 帧同步, 用户感知一致.
            session_status.set_status(sid, "idle")
            entry = pool.peek_entry(sid) if pool is not None else None
            if entry is not None and entry.pending_rebuild:
                logger.info(f"weaver rebuild pending — evict session={sid}")
                try:
                    await pool.evict(sid)
                except Exception:
                    logger.exception(f"weaver post-turn evict failed (session={sid})")


async def _run_chat_turn(
    *,
    sid: str,
    mounted: list[str],
    display_text: str,
    internal_prompt: str,
    attachment_count: int,
    inline_image_count: int = 0,
    inline_image_blocks: list[dict[str, Any]] | None = None,
    approval_mode: str | None = None,
    pool,
) -> StreamingResponse:
    """LoomPool 拉 client → StreamBuffer 占名额 → 起后台 query task → 返 SSE.

    /chat 跟 /chat/with-attachments 共享这条流水线. 区别仅在入参装配:
      - /chat: display_text == internal_prompt, 0 附件 0 图片
      - /chat/with-attachments: internal_prompt 已被 prepend 上
        <pentaloom_internal_attachments> 块; display_text 是用户纯文本
        (附件 only 时为空字符串); attachment_count > 0.
        inline_image_blocks 不为 None 时走 multimodal 路径喂 SDK.

    抽出来的理由: 上轮 weaver_post_turn_evict + HITL pending bookkeeping +
    SSE 状态机 + StreamBuffer 生命周期都嵌在这里, 复制粘贴一份就开始漂.

    `pool.get` 含 SDK CLI 子进程 spawn + initialize, 新 session 数百 ms ~ 数秒.
    这段时间不能挡前端 fetch — 否则 mutate("sessions") 调不上, 新会话不在 sidebar
    出现. 所以先 buf.append 一个 sentinel + 起后台 task 跑 pool.get, 立刻返
    StreamingResponse; stream_all 第一次 yield 是 sentinel, headers 立即 flush.
    """
    buf = stream_buffers.create_for_turn(sid)
    buf.set_user_prompt(
        display_text,
        attachment_count=attachment_count,
        inline_image_count=inline_image_count,
    )
    # SSE comment 帧: `:` 开头任意文本 + `\n\n`. 浏览器 EventSource 自动忽略,
    # 我们的 parseSSE (api.ts) 也只 filter `data:` 开头的行, 不会当 frame 消费.
    # 作用: 让 stream_all 立刻有东西 yield → uvicorn flush response headers
    # → 前端 `await fetch()` 立即 resolve → mutate("sessions") 立即调上.
    buf.append(": session-ready\n\n")
    # session 级状态推 running — 跟 sentinel 同步, 用户按下发送瞬间 sidebar
    # 的 spinner 立刻转, 不等 SDK 子进程 spawn 完.
    session_status.set_status(sid, "running")

    async def _kickoff() -> None:
        # 后台跑 pool.get (SDK 子进程 spawn) + query. 异常落 SSE 帧 + 状态切 idle.
        try:
            pl, lock = await pool.get(sid, mounted, approval_mode=approval_mode)
        except ValueError as e:
            buf.append(_sse({"type": "error", "message": str(e)}))
            buf.append(_sse({"type": "stream_end"}))
            buf.finish()
            session_status.set_status(sid, "idle")
            return
        except Exception as e:
            logger.exception(f"pool.get failed sid={sid}: {e}")
            buf.append(_sse({"type": "error", "message": f"session init failed: {e}"}))
            buf.append(_sse({"type": "stream_end"}))
            buf.finish()
            session_status.set_status(sid, "idle")
            return
        await _run_query_and_fill_buffer(
            pl, internal_prompt, inline_image_blocks, sid, lock, pool
        )

    task = asyncio.create_task(_kickoff())
    buf.set_task(task)

    return StreamingResponse(
        buf.stream_all(),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Session-Id": sid},
    )


@router.post("/chat", summary="向 PentaLoom 发一条消息, SSE 流回 (支持后续 GET /resume 重连)")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(500, "LoomPool not initialized")

    sid, mounted = await _resolve_session(req)

    return await _run_chat_turn(
        sid=sid,
        mounted=mounted,
        display_text=req.prompt,
        internal_prompt=req.prompt,
        attachment_count=0,
        approval_mode=req.approval_mode,
        pool=pool,
    )


# ──── 附件 / inline image 上限 (跟 docs/attachment-upload-plan.md §8 对齐) ─────
MAX_ATTACHMENT_COUNT = 10
MAX_PER_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
# inline image (粘贴) 单独 cap — 不落盘 + 不算 file count, 直接喂 SDK content block.
# Anthropic 接图大约 5MB / 张, 总 token 隐含 cap; 我们粗校 5MB / 张 + 10 张.
MAX_INLINE_IMAGE_COUNT = 10
MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024
_INLINE_IMAGE_MIME_ALLOW = {"image/png", "image/jpeg", "image/gif", "image/webp"}


@router.post(
    "/chat/with-attachments",
    summary="multipart 版 /chat — 把附件 commit 进 sandbox 后再起 turn",
)
async def chat_with_attachments(
    request: Request,
    prompt: Annotated[str, Form()] = "",
    session_id: Annotated[str | None, Form()] = None,
    mounted_dirs: Annotated[str | None, Form()] = None,
    approval_mode: Annotated[str | None, Form()] = None,  # 首条消息切 mode 用
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006 (FastAPI 默认惯例)
    inline_images: Annotated[list[UploadFile], File()] = [],  # noqa: B006
) -> StreamingResponse:
    """commit-on-send 附件 / inline image 上传. 两条路径:
      - files: 落盘 sandbox/attachments/{filename}, 走 internal block 引用路径
      - inline_images: **不落盘**, 转 base64 拼 Anthropic content_blocks 直接喂 SDK
        (走 PentaLoom.query_multimodal). LLM 真"看到"图. 适合粘贴截图.

    流程:
      1. count cap (file ≤ 10, inline image ≤ 10)
      2. 解 mounted_dirs JSON (form 字段编码限制)
      3. 复用 _resolve_session — 构造 fake ChatRequest 带过去
      4. 流式落盘 file 到 sandbox/attachments/{filename}, 同名 (N) suffix 避让
      5. 读 inline_images 转 base64 (per-image size cap; mime 白名单)
      6. 拼 <pentaloom_internal_attachments> block 跟 user 文本 → internal_prompt
      7. 走 _run_chat_turn — inline_images 不为空时走 multimodal 路径

    HITL: 不审 — 见 plan §4.2 lead note. 用户主动通过 file picker / clipboard,
    跟 mount_dir 同档信任.
    """
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(500, "LoomPool not initialized")

    if not files and not inline_images:
        raise HTTPException(
            400, "no files or inline_images; use /chat for text-only sends"
        )
    if len(files) > MAX_ATTACHMENT_COUNT:
        raise HTTPException(
            400, f"too many files ({len(files)} > {MAX_ATTACHMENT_COUNT})"
        )
    if len(inline_images) > MAX_INLINE_IMAGE_COUNT:
        raise HTTPException(
            400,
            f"too many inline images ({len(inline_images)} > {MAX_INLINE_IMAGE_COUNT})",
        )

    # parse mounted_dirs JSON. 空字符串 / 缺字段 = None (沿用 db).
    mounted_dirs_list: list[str] | None
    if mounted_dirs:
        try:
            parsed = json.loads(mounted_dirs)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"mounted_dirs not valid JSON: {e}") from e
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise HTTPException(400, "mounted_dirs must be a JSON array of strings")
        mounted_dirs_list = parsed
    else:
        mounted_dirs_list = None

    # resolve / create session — 用 fake ChatRequest 复用现有逻辑.
    # prompt 走过去仅用于算 title (新 session 时); 附件 only 时空 prompt 也 OK,
    # title 为 None, sidebar 退化到 sid 截断显示.
    fake_req = ChatRequest(
        prompt=prompt, session_id=session_id, mounted_dirs=mounted_dirs_list
    )
    sid, mounted = await _resolve_session(fake_req)

    # commit files 流式落盘到 sandbox/attachments/{name}, 同名加 (N) suffix.
    # 失败时只清理这一轮新写入的文件; 不动 attachments/ 里上轮 / 别 turn 的文件.
    settings = get_settings()
    sandbox = settings.sandbox_dir_for(sid)
    attachments_dir = sandbox / "attachments"
    written_dests: list[Path] = []  # 失败时回滚用
    rel_paths: list[str] = []
    total_written = 0
    try:
        for i, f in enumerate(files):
            safe = sanitize_filename(f.filename or "", fallback=f"untitled-{i + 1}")
            dest = pick_unique_dest(attachments_dir, safe)
            written = await commit_attachment(
                f,
                dest=dest,
                per_file_max=MAX_PER_FILE_BYTES,
                total_so_far=total_written,
                total_max=MAX_TOTAL_BYTES,
            )
            total_written += written
            written_dests.append(dest)
            rel_paths.append(f"attachments/{dest.name}")
    except AttachmentTooLarge as e:
        for d in written_dests:
            d.unlink(missing_ok=True)
        raise HTTPException(400, str(e)) from e
    except OSError as e:
        for d in written_dests:
            d.unlink(missing_ok=True)
        logger.exception(f"attachment commit failed sid={sid}")
        raise HTTPException(500, f"attachment commit failed: {e}") from e

    if files:
        logger.info(
            f"attachments committed sid={sid} count={len(files)} bytes={total_written} "
            f"paths={rel_paths}"
        )

    # 读 inline_images: 不落盘, 直接转 base64 给 SDK content block 用.
    # 单张超 cap / mime 不在白名单 → 400. 整 turn 已写入的附件 attachments_dir
    # 不回滚 (image 校验在 file 写入之后, 出错时附件保留 — 用户选择重试整 turn 时
    # 自动覆盖同名 + 加 suffix).
    inline_image_blocks: list[dict[str, Any]] = []
    for i, img in enumerate(inline_images):
        mime = (img.content_type or "image/png").lower()
        if mime not in _INLINE_IMAGE_MIME_ALLOW:
            raise HTTPException(
                400,
                f"inline_image[{i}] mime {mime!r} not allowed; "
                f"must be one of {sorted(_INLINE_IMAGE_MIME_ALLOW)}",
            )
        # 一次 read 进内存. UploadFile 默认有 SpooledTemporaryFile, 文件大时已落盘.
        # cap 在前面 5MB, 内存压力可控.
        data = await img.read()
        if len(data) > MAX_INLINE_IMAGE_BYTES:
            raise HTTPException(
                400,
                f"inline_image[{i}] {img.filename or '(unnamed)'!r} too large: "
                f"{len(data)} > {MAX_INLINE_IMAGE_BYTES}",
            )
        inline_image_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": base64.b64encode(data).decode(),
            },
        })

    if inline_images:
        total_img_bytes = sum(
            len(base64.b64decode(b["source"]["data"])) for b in inline_image_blocks
        )
        logger.info(
            f"inline images sid={sid} count={len(inline_images)} bytes={total_img_bytes}"
        )

    # 拼 internal_prompt. display_text 保持纯用户文本 (空时前端走 count 占位).
    # internal_prompt 在 prompt 空时就 = block 自身 — block 是 markdown 文本, SDK
    # 接受这种 user message; strip 后剩空串, 前端走 attachment_count > 0 占位渲染.
    # files 空时不拼 attachments block (纯 inline image 场景, 没文件路径要 agent 看).
    if rel_paths:
        block = build_attachments_block(rel_paths)
        internal_prompt = f"{block}{prompt}"
    else:
        internal_prompt = prompt

    return await _run_chat_turn(
        sid=sid,
        mounted=mounted,
        display_text=prompt,
        internal_prompt=internal_prompt,
        attachment_count=len(files),
        inline_image_count=len(inline_images),
        inline_image_blocks=inline_image_blocks or None,
        approval_mode=approval_mode,
        pool=pool,
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
    #
    # attachment_count / inline_image_count 跟 text 一对配套: text 空 + 任一 > 0
    # 时前端渲染 "📎 N 个文件" / "🖼️ N 张图片" 占位; 都为 0 + text 空时不注入帧.
    user_prompt = buf.user_prompt
    attachment_count = buf.attachment_count
    inline_image_count = buf.inline_image_count
    stream = buf.stream_all()

    async def _with_user_prompt() -> AsyncIterator[str]:
        if user_prompt or attachment_count > 0 or inline_image_count > 0:
            yield _sse({
                "type": "user_prompt",
                "text": user_prompt or "",
                "attachment_count": attachment_count,
                "inline_image_count": inline_image_count,
            })
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
        # race 修后理论不该触发 (前端只在 register 后才弹审批栏), 但留此分支
        # 容错重复点击 / 网络重试 / 已 resolve 后的二次 POST. 静默 ack 不抛错,
        # 防止前端 toast 报"审批不存在". 注意: 后续 add_mounted_dir 抛的 ValueError
        # → HTTPException(404) 是真错 (用户传了非法 path), 那条不动.
        return {"status": "already_resolved"}

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


class ApprovalModeBody(BaseModel):
    mode: str


@router.get(
    "/chat/{sid}/approval-mode",
    summary="读会话当前审批模式 (default / auto / full_access)",
)
async def get_approval_mode(sid: str, request: Request) -> dict[str, str]:
    """前端 picker 初始化 / 切换会话 tab 时调. 会话不存在 (尚未 build) 返 default."""
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(500, "LoomPool not initialized")
    mode = pool.get_approval_mode(sid)
    return {"mode": mode or "default"}


@router.patch(
    "/chat/{sid}/approval-mode",
    summary="切换会话审批模式 (per-session 仅内存, evict 后丢失)",
)
async def set_approval_mode(
    sid: str, body: ApprovalModeBody, request: Request,
) -> dict[str, str]:
    """切换立刻被 can_use_tool closure 读到, 不 rebuild client.

    语义: 已经 await fut 等审的请求不受影响 — 用户必须答完它们; 之后进的
    工具调用走新模式. 切到 full_access 不会自动放行已弹的 pending.
    """
    if body.mode not in APPROVAL_MODES:
        raise HTTPException(
            422, f"mode must be one of {list(APPROVAL_MODES)}",
        )
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(500, "LoomPool not initialized")
    ok = pool.set_approval_mode(sid, body.mode)
    if not ok:
        # session 还没 build (用户刚开新对话还没发第一条消息) — 暂时不支持预设.
        # 前端可以先存本地, 第一次 send 之后再 PATCH.
        raise HTTPException(404, "session not active; send a message first")
    return {"mode": body.mode}


@router.post(
    "/chat/{sid}/stop",
    summary="中断当前 turn (用户主动停)",
)
async def stop_chat(sid: str, request: Request) -> Response:
    """只发 SDK interrupt — 让 CLI 子进程主动停 + 给挂起的 tool_use 发 cancellation
    tool_result + 推一条 [Request interrupted] marker message + ResultMessage,
    stream task 自然走完 finally 段 (stream_end + finish).

    **不要**提前 buf.cancel() — 那会把上面 SDK 的 cancellation message stream 吞掉,
    前端只看到工具半截没了, 没"refused" 视觉信号, mutate history 还有 race.
    Idempotent: 重复 stop 同 sid 不报错, 返 204.
    """
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(500, "LoomPool not initialized")
    entry = pool.peek_entry(sid)
    if entry is None:
        return Response(status_code=204)

    try:
        await entry.pl.client.interrupt()
        logger.info(f"chat interrupt sent sid={sid}")
    except Exception as e:
        logger.warning(f"interrupt failed sid={sid}: {e}")

    return Response(status_code=204)
