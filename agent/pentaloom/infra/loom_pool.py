"""LoomPool — per-session PentaLoom 实例池.

为什么有这个东西:
  ClaudeSDKClient = 一个 CLI 子进程 = 一个 stdio 通道, 同时只能跑一次 query.
  多会话桌面 app 要求 tab A / tab B 同时聊不互阻, 所以每个 session 需要独立 client.

接口:
  pool.get(session_id, mounted_dirs) -> (PentaLoom, asyncio.Lock)
    没有就建 (调 PentaLoom.__aenter__ 起子进程), 有就复用 + 更新 LRU.
    返回的 lock 是 per-session, 锁住该 session 内 turn 串行 (仍是单 client 限制),
    但不挡别的 session.
    主 cwd 永远是 settings.sandbox_dir_for(sid), 用户挂载的目录走 add_dirs.

  pool.rebuild(session_id, mounted_dirs)
    阶段 2 用: 用户授权新挂载点后, 主动重建 client (旧的 __aexit__ + 新的 __aenter__
    用 SDK resume 接回内存上下文). 由 add_mounted_dir 工具同意路径后触发.

  pool.shutdown()
    lifespan 退出时调, 关掉所有子进程.

容量:
  超过 max_size 触发 LRU evict (__aexit__ 关该 session 的 client).
  暂不上 idle TTL, 看实际负载再加.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from claude_agent_sdk import AgentDefinition
from loguru import logger

from pentaloom.app import PentaLoom
from pentaloom.config import get_settings
from pentaloom.tools import PERMISSION_REGISTRY, make_can_use_tool


def _validate_sid(session_id: str) -> None:
    """SDK 强校验: session_id 必须是合法 UUID, 否则 CLI spawn 时直接挂.
    在 pool 入口拦下, 给上层清晰错误.
    """
    try:
        uuid.UUID(session_id)
    except (ValueError, AttributeError, TypeError) as e:
        raise ValueError(
            f"session_id must be a valid UUID, got {session_id!r}"
        ) from e


@dataclass
class _Entry:
    pl: PentaLoom
    mounted_dirs: list[str]  # 当前 client 起的时候用的挂载列表, 跟 db 比对决定要不要重建
    # Bash HITL 会话级白名单: cmd 字符串集合. make_can_use_tool 闭包持的就是这个
    # set, router 在 allow_session 时通过 LoomPool.add_bash_allowed 往里加, 引用
    # 共享, 不需要 rebuild client. Entry evict 时随 dataclass 一起 gc, 不持久化.
    bash_allowlist: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)


class LoomPool:
    def __init__(
        self,
        *,
        agents: dict[str, AgentDefinition] | None = None,
        max_size: int = 8,
    ) -> None:
        self._agents = agents or {}
        self._max = max_size
        self._registry: dict[str, _Entry] = {}
        self._settings = get_settings()
        # 防并发 get 同一新 sid 重复建 (两个请求同时来, 第二个该等第一个建好复用).
        self._build_lock = asyncio.Lock()

    async def get(
        self, session_id: str, mounted_dirs: list[str]
    ) -> tuple[PentaLoom, asyncio.Lock]:
        _validate_sid(session_id)
        async with self._build_lock:
            entry = self._registry.get(session_id)
            if entry is None:
                if len(self._registry) >= self._max:
                    await self._evict_lru()
                # 首次 build: 用 session_id=. 之后重建 (mounts 变 / evict 后再 get) 走 resume.
                # SDK 限制: 同一 sid 的 JSONL 已存在时, 再用 session_id= spawn 会 exit 1,
                # 必须 resume=. 我们在 build 时根据沙箱目录是否已存在判断 (首次还是续接).
                resume_existing = self._settings.sandbox_dir_for(session_id).exists()
                entry = await self._build(session_id, mounted_dirs, resume=resume_existing)
                self._registry[session_id] = entry
            elif sorted(entry.mounted_dirs) != sorted(mounted_dirs):
                logger.info(
                    f"LoomPool rebuild session={session_id} mounts {entry.mounted_dirs} -> {mounted_dirs}"
                )
                await entry.pl.__aexit__(None, None, None)
                # rebuild 时保留 bash_allowlist — 重建动机是挂载目录变了, 跟用户对
                # bash 命令的信任无关, 没必要让他再点一遍同样的 cmd.
                old_allowlist = entry.bash_allowlist
                entry = await self._build(
                    session_id, mounted_dirs, resume=True, bash_allowlist=old_allowlist
                )
                self._registry[session_id] = entry
            else:
                entry.last_used = time.monotonic()
        return entry.pl, entry.lock

    async def _build(
        self,
        session_id: str,
        mounted_dirs: list[str],
        *,
        resume: bool,
        bash_allowlist: set[str] | None = None,
    ) -> _Entry:
        sandbox = self._settings.sandbox_dir_for(session_id)
        sandbox.mkdir(parents=True, exist_ok=True)
        # set 必须先建好再传给 make_can_use_tool, 让 closure 跟 _Entry 持同一引用 —
        # 之后 router add_bash_allowed 改 set 才能立刻被 can_use_tool 读到.
        allowlist = bash_allowlist if bash_allowlist is not None else set()
        pl = PentaLoom(
            agents=self._agents,
            session_id=None if resume else session_id,
            resume=session_id if resume else None,
            cwd=sandbox,
            add_dirs=list(mounted_dirs),
            can_use_tool=make_can_use_tool(session_id, bash_allowlist=allowlist),
        )
        await pl.__aenter__()
        # rebuild 时 sid 已在 _registry, size 应取 len(); 首次 build 时 caller 还没插
        # 入 sid, 才需要 +1. 区分: resume=True 走 rebuild 路径.
        projected_size = len(self._registry) if resume else len(self._registry) + 1
        logger.info(
            f"LoomPool built session={session_id} sandbox={sandbox} "
            f"mounts={mounted_dirs} resume={resume} (size={projected_size})"
        )
        return _Entry(pl=pl, mounted_dirs=list(mounted_dirs), bash_allowlist=allowlist)

    def add_bash_allowed(self, session_id: str, cmd: str) -> bool:
        """会话 Bash 白名单加一条 cmd. 给 chat_permission router 走 allow_session 时调.

        返回 True 表示新加, False 表示 session 不存在 (一般是 evict 后才会出现 —
        理论上不可能, 因为 pending future 是 evict 时被 deny 的).
        """
        entry = self._registry.get(session_id)
        if entry is None:
            return False
        entry.bash_allowlist.add(cmd)
        return True

    async def evict(self, session_id: str) -> None:
        entry = self._registry.pop(session_id, None)
        if entry is None:
            return
        # 先清掉该 session 所有 pending 授权 (set Future as deny),
        # 防止 can_use_tool 协程永远 await; 再关 client.
        PERMISSION_REGISTRY.cleanup_session(session_id)
        await entry.pl.__aexit__(None, None, None)
        logger.info(f"LoomPool evicted session={session_id}")

    async def _evict_lru(self) -> None:
        oldest_sid = min(self._registry, key=lambda k: self._registry[k].last_used)
        await self.evict(oldest_sid)

    async def shutdown(self) -> None:
        for sid in list(self._registry.keys()):
            try:
                await self.evict(sid)
            except Exception:
                logger.exception(f"LoomPool shutdown failed for session={sid}")

    @property
    def size(self) -> int:
        return len(self._registry)
