"""per-session SSE 帧缓冲 + 多订阅者广播.

为什么需要:
  POST /chat 直接返 StreamingResponse 时, 客户端 abort (刷新 / 切 session)
  会让 generate() 协程被 cancel, 进而 pl.query() 被 cancel — 但 PentaLoom 一轮
  turn 可能跑几十秒, 用户切走再回来本就该看到进度, 而且 HITL pending 还挂着
  asyncio.Future, 没人来取就死锁直到 evict.

设计 (照 Ling-Agent / krow-agent 的 StreamBuffer 抄):
  - per-session 一个 StreamBuffer, 累积本轮 turn 已 emit 的所有 SSE chunk (str).
  - chunks 是已编码好的完整 "data: {...}\n\n", 不再二次 serialize.
  - subscribers: list[asyncio.Queue] — 每个连接一个 queue, 不互相干扰.
  - stream_all(): 唯一对外的重连接口. 重放时把同 (msg_uuid, index) 的
    text_delta / thinking_delta 折叠成完整 text / thinking frame, 续订阅段仍以
    raw delta 推送. 前端 reducer 按 (msg_uuid, index) 幂等 merge, 重放安全.
  - _pending_approval_chunk: 当前挂着等审批的 tool_use SSE chunk. 折叠重放时
    自动包含在 chunks 里, 不需要额外注入.
    (PERMISSION_REGISTRY 里的 Future 本来就是 in-memory 共享的, 任何 HTTP
    都能 resolve, 所以协议层不用动 — 只要前端能渲出按钮.)

生命周期:
  - StreamBufferManager.create_for_turn(sid) — 新 turn 开始时调, 覆盖旧的.
  - buffer.set_task(task) — POST /chat 把跑 query 的后台 task 注册进来.
  - buffer.append(chunk) — 跑 query 的 task 每出一帧调一次.
  - buffer.finish() — turn 结束 (ResultMessage / stream_end / error / cancel) 调.
  - StreamBufferManager.remove(sid) — session delete / LoomPool evict 时调, 顺便 cancel task.

关于持久化:
  - 不写库. PentaLoom 已经有 SDK JSONL 镜像在 SQLite (SessionStore), turn 结束后
    /sessions/{sid}/messages 能读全, 这里 buffer 只覆盖"正在跑的这轮 turn".
  - 重启进程 = 当前 turn 数据丢光, 但 LoomPool 的 SDK 子进程也跟着死了, 没有
    可恢复的执行流, 接得回来的只有 JSONL 历史, 等于 turn 已经"以失败告终".
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger


class StreamStatus(Enum):
    STREAMING = "STREAMING"
    COMPLETE = "COMPLETE"


def _parse_sse_frame(chunk: str) -> dict | None:
    """从 SSE chunk 字符串 ("data: {...}\n\n") 解出 frame dict. 解析失败返 None.

    只在重放折叠时调用 (低频), 性能不敏感. 一字不差的逆操作 (chat.py:_sse).
    """
    line = chunk.strip()
    if not line.startswith("data: "):
        return None
    try:
        return json.loads(line[6:])
    except json.JSONDecodeError:
        return None


def _encode_sse(frame: dict) -> str:
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"


def _fold_delta_for_replay(chunks: list[str]) -> list[str]:
    """把 chunks 里同 (msg_uuid, index) 的 text_delta / thinking_delta 折叠成
    一个完整 text / thinking frame (streaming=true).

    返回的 list 顺序: 完整 frame 出现在该 (msg_uuid, index) 第一个 delta 原本的
    位置; 其它类型 (tool_use / tool_result / task_* / result / error / stream_end /
    user_prompt) 按原序穿插.

    幂等保证: 多次调用同样 chunks 输出一致; 同时支持续订阅段继续推 raw delta
    (前端会按 (msg_uuid, index) 追加到对应已 settle 的完整 frame 上).
    """
    out: list[str] = []
    # (msg_uuid, index) → 该完整 frame 在 out 里的下标 + 类型 ("text"/"thinking")
    folded: dict[tuple[str, int], tuple[int, str]] = {}

    for chunk in chunks:
        frame = _parse_sse_frame(chunk)
        if frame is None:
            out.append(chunk)
            continue
        ftype = frame.get("type")
        if ftype in ("text_delta", "thinking_delta"):
            kind = "text" if ftype == "text_delta" else "thinking"
            msg_uuid = frame.get("msg_uuid") or ""
            index = int(frame.get("index", 0))
            key = (msg_uuid, index)
            text = frame.get("text", "")
            if key in folded:
                pos, _ = folded[key]
                existing = _parse_sse_frame(out[pos]) or {}
                existing["text"] = existing.get("text", "") + text
                out[pos] = _encode_sse(existing)
            else:
                merged = {
                    "type": kind,
                    "text": text,
                    "msg_uuid": msg_uuid,
                    "index": index,
                    "streaming": True,
                }
                folded[key] = (len(out), kind)
                out.append(_encode_sse(merged))
        else:
            out.append(chunk)
    return out


@dataclass
class StreamBuffer:
    """单个 session 当前 turn 的 SSE 帧缓冲. 多订阅者广播."""

    chunks: list[str] = field(default_factory=list)
    status: StreamStatus = StreamStatus.STREAMING
    _subscribers: list[asyncio.Queue[str | None]] = field(default_factory=list)
    _task: asyncio.Task | None = None
    # 当前 pending 的工具审批 chunk. tool_use 帧来时 set, 对应 tool_result /
    # error / stream_end 来时清. subscribe() 时若非 None 先注入, 让后到的连接
    # 也能渲审批 UI.
    _pending_approval_chunk: str | None = None
    # tool_use_id → chunk, 多个 pending (多 Bash 并发) 也支持. _pending_approval_chunk
    # 取 dict 最后一个用于兼容旧字段名, 实际订阅时全部注入.
    _pending_approval_chunks: dict[str, str] = field(default_factory=dict)
    # 这轮 turn 的用户原始 prompt — POST /chat 时 set, finish 时清.
    # 重连场景 (subscribe_only=true): 用户原 prompt 既不在 buffer.chunks 里
    # (UserMessage 文本不走 _serialize), 也未必在 JSONL 里 (SDK 写入时机晚),
    # 不注入的话刷新页面会看不到自己刚发的那条. 跟 _pending_approval_chunks
    # 同款 snapshot 注入逻辑解决.
    user_prompt: str | None = None

    def set_task(self, task: asyncio.Task) -> None:
        self._task = task

    def set_user_prompt(self, prompt: str) -> None:
        self.user_prompt = prompt

    def cancel(self) -> bool:
        """取消后台 query task. 返回 True 表示真的取消了 (本来在跑)."""
        if self._task and not self._task.done():
            self._task.cancel()
            return True
        return False

    def mark_pending(self, tool_use_id: str, chunk: str) -> None:
        """记一条 pending 审批 chunk. tool_use 帧 emit 完调."""
        self._pending_approval_chunks[tool_use_id] = chunk
        self._pending_approval_chunk = chunk

    def clear_pending(self, tool_use_id: str) -> None:
        """对应 tool_result / error / stream_end 时调, 清掉."""
        self._pending_approval_chunks.pop(tool_use_id, None)
        if not self._pending_approval_chunks:
            self._pending_approval_chunk = None
        else:
            # 仍有别的 pending, _pending_approval_chunk 任挑一个 (兼容)
            self._pending_approval_chunk = next(
                iter(self._pending_approval_chunks.values())
            )

    def append(self, chunk: str) -> None:
        """加一条 SSE chunk 并推所有订阅者. chunk 必须是完整的 "data: ...\\n\\n"."""
        self.chunks.append(chunk)
        for queue in self._subscribers:
            queue.put_nowait(chunk)

    def finish(self) -> None:
        """标记 turn 完成, 给所有订阅者推 None 通知收尾."""
        if self.status == StreamStatus.COMPLETE:
            return
        self.status = StreamStatus.COMPLETE
        # turn 结束所有 pending 自然失效 (要么已 resolve 要么会被 evict cleanup)
        self._pending_approval_chunks.clear()
        self._pending_approval_chunk = None
        for queue in self._subscribers:
            queue.put_nowait(None)

    async def stream_all(self) -> AsyncIterator[str]:
        """重连/首连用: 重放 chunks 全集 (delta 折叠) + 订阅后续新增 (raw).

        重放阶段不发 raw text_delta / thinking_delta, 而是把同 (msg_uuid, index)
        的 delta 折叠成完整 text / thinking frame (streaming=true) 一次性发出.
        这样:
          - 前端 reducer 按 (msg_uuid, index) 幂等: 同 id 完整 frame 覆盖, 不双份
          - 续订阅段仍以 raw delta 推, 前端按 (msg_uuid, index) 追加到对应 frame
          - 多次切走再回来, 每次拿到的都是"该 turn 至当下的完整 snapshot"

        其它类型 (tool_use / tool_result / task_* / result / error / stream_end)
        按原序穿插发出, 不动. 完整 text/thinking frame 的位置 = 该 (msg_uuid, index)
        第一个 delta 出现的位置.

        竞态点: 注册 + snapshot 在同一同步块, 不会漏不会重.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._subscribers.append(queue)
        replay = _fold_delta_for_replay(self.chunks)
        try:
            for chunk in replay:
                yield chunk
            if self.status == StreamStatus.COMPLETE:
                while not queue.empty():
                    chunk = queue.get_nowait()
                    if chunk is not None:
                        yield chunk
                return
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            try:
                self._subscribers.remove(queue)
            except ValueError:
                pass


class StreamBufferManager:
    """全局 per-session buffer 池.

    生命周期: create_for_turn (POST /chat) → 一次 turn 跑完 → buffer 留着等
    后续重连 (前端进页面 GET /resume 还能拉到完整回放); 下次 POST /chat 时
    create_for_turn 覆盖. 真正清理在 session delete / LoomPool evict 时 remove.
    """

    def __init__(self) -> None:
        self._buffers: dict[str, StreamBuffer] = {}

    def get(self, session_id: str) -> StreamBuffer | None:
        return self._buffers.get(session_id)

    def create_for_turn(self, session_id: str) -> StreamBuffer:
        """开新 turn 前调. 如果上一轮还有 buffer (理论上应已 finish), 先 cancel
        task + finish, 再覆盖."""
        old = self._buffers.get(session_id)
        if old is not None:
            if old.status == StreamStatus.STREAMING:
                logger.warning(
                    f"StreamBuffer: overwriting still-streaming buffer for {session_id[:8]}"
                )
                old.cancel()
                old.finish()
        buf = StreamBuffer()
        self._buffers[session_id] = buf
        return buf

    def is_streaming(self, session_id: str) -> bool:
        buf = self._buffers.get(session_id)
        return buf is not None and buf.status == StreamStatus.STREAMING

    def remove(self, session_id: str) -> None:
        """session delete / evict 时调. cancel + finish + 弹出."""
        buf = self._buffers.pop(session_id, None)
        if buf is None:
            return
        if buf.status == StreamStatus.STREAMING:
            buf.cancel()
            buf.finish()


stream_buffers = StreamBufferManager()
