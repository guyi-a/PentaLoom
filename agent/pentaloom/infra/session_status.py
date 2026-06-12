"""session 级状态 publisher — 给左侧 sidebar 实时反馈用.

为什么单独抽 (跟 chat StreamBuffer 分开):
  StreamBuffer 是 per-turn 的 SSE 帧缓冲, 服务于 ChatStream 主对话区.
  sidebar 关心的不是消息内容, 而是会话级状态 (在跑 / 空闲 / 等审批 / unread),
  跨所有 session 共享一条全局长连. 跟 chat 流物理分开, 避免:
    1. 新订阅者要从一堆 chat 帧里筛 status
    2. sidebar 监听一条 chat 流就只能看一个 session 的状态
    3. SSE 流跟 turn 生命周期绑定, sidebar 想看"全部" 没法拿

设计:
  - asyncio.Queue 多订阅者广播:
    * 每个 SSE 长连 = 一个 queue 订阅
    * set_status 时把 event 推给 all 订阅者
    * 客户端断开 → unsubscribe (queue 从 list 移除)
  - _registry: dict[sid, status] — current status per sid
  - 新订阅者订阅瞬间 dump _registry 全集 (非 idle 的), 让 sidebar 立刻拿到现状
    不需要等下次 status 变化
  - publish 完全 in-memory, 进程重启丢, 但 sidebar 重连时 SSE 重新订阅 + dump
    snapshot, 自然恢复

事件 schema (跟前端 lib/session-status-store.ts 协议对齐):
  { "type": "status", "sid": str, "status": "running"|"idle"|"waiting_approval" }
  状态语义:
    - running:           SDK 子进程正在跑 turn
    - waiting_approval:  HITL 工具弹审批等待用户决定
    - idle:              非 running / 非 waiting_approval (默认值, 不进 _registry)
  其它状态 (unread / 等) 留待 follow-up. 当前 MVP 三态.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal

from loguru import logger

SessionStatus = Literal["running", "idle", "waiting_approval"]


def _sse(frame: dict) -> str:
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"


class SessionStatusPublisher:
    """全局单例. 跟 stream_buffers 同款 module-level singleton (见 stream_buffer.py)."""

    def __init__(self) -> None:
        # 仅记录非 idle 状态. set_status(sid, "idle") 直接从 dict 删掉, 让 dump
        # snapshot 时不带 idle (前端拿不到也按 idle 处理).
        self._registry: dict[str, SessionStatus] = {}
        self._subscribers: list[asyncio.Queue[str | None]] = []

    def get(self, sid: str) -> SessionStatus:
        return self._registry.get(sid, "idle")

    def set_status(self, sid: str, status: SessionStatus) -> None:
        """更新 sid 状态 + broadcast 给所有订阅者. 跟前一状态相同时仍 broadcast —
        让 sidebar 在多 tab 同步场景下保险, 不依赖客户端去重."""
        if status == "idle":
            self._registry.pop(sid, None)
        else:
            self._registry[sid] = status
        chunk = _sse({"type": "status", "sid": sid, "status": status})
        for q in self._subscribers:
            q.put_nowait(chunk)

    def remove(self, sid: str) -> None:
        """session 删除时调 — broadcast 一条 idle, 同时清 registry. 让 sidebar
        即使在该 session 状态本来就是 idle 的情况下也能感知"已被删, 别再渲".
        实际"删"的逻辑在 sessions delete endpoint, 这里只管广播."""
        had = self._registry.pop(sid, None)
        if had is not None:
            chunk = _sse({"type": "status", "sid": sid, "status": "idle"})
            for q in self._subscribers:
                q.put_nowait(chunk)

    async def subscribe(self) -> AsyncIterator[str]:
        """SSE handler 调这个. 先 yield 当前所有非 idle 状态 snapshot, 然后阻塞
        等队列新事件. caller 退出 (客户端断开) 时 finally 清理订阅."""
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._subscribers.append(queue)
        logger.debug(f"session_status: subscriber +1 (total={len(self._subscribers)})")
        try:
            # snapshot 当前状态 — 给新订阅者补齐"我接进来时已经在跑的会话".
            for sid, status in list(self._registry.items()):
                yield _sse({"type": "status", "sid": sid, "status": status})
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
            logger.debug(
                f"session_status: subscriber -1 (total={len(self._subscribers)})"
            )

    def shutdown(self) -> None:
        """lifespan 退出时调, 给所有订阅者推 None 让 generator 收尾."""
        for q in self._subscribers:
            q.put_nowait(None)
        self._subscribers.clear()
        self._registry.clear()


session_status = SessionStatusPublisher()
