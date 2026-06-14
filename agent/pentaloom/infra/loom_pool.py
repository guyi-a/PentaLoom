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
from claude_agent_sdk._internal.sessions import project_key_for_directory
from loguru import logger

from pentaloom.app import PentaLoom
from pentaloom.capabilities.weaver import assemble_weaver
from pentaloom.config import get_settings
from pentaloom.infra.approval.policy import APPROVAL_MODES, ApprovalModeRef
from pentaloom.infra.session_store import SQLiteSessionStore
from pentaloom.infra.stream_buffer import stream_buffers
from pentaloom.tools import PERMISSION_REGISTRY, make_can_use_tool
from pentaloom.tools.weaver import WEAVER_MCP_SERVER_NAME, build_weaver_mcp_server


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
    # HITL 会话级免审表: tool_name → set(免审 key). make_can_use_tool 闭包持的就是
    # 这个 dict, router 在 allow_session 时通过 LoomPool.add_hitl_allowed 往里加,
    # 引用共享, 不需要 rebuild client. Entry evict 时随 dataclass 一起 gc, 不持久化.
    # 例: {"Bash": {"ls -al"}, "mcp__pentaloom_env__install_python_libs": {"numpy\nopenpyxl"}}
    hitl_allowlists: dict[str, set[str]] = field(default_factory=dict)
    # 审批模式 ref — make_can_use_tool 闭包跟 _Entry 共享同一引用对象.
    # 改 .value 立刻被 closure 读到, 不需要 rebuild. 全局 settings 变更走
    # broadcast_approval_mode, per-session 临时切换走 set_approval_mode.
    approval_mode_ref: ApprovalModeRef = field(default_factory=ApprovalModeRef)
    # weaver hot reload (Spike 1+2+3 verified): weave_* / edit_weaver / delete_weaver
    # 成功时设 True, 当前 turn 的 stream_end 之后 chat router 调 pool.evict(sid).
    # 用户下条 message 触发 LoomPool.get → resume rebuild, 新 weaver 内容生效.
    # 必须**推迟到 stream_end** 而不是 weave_* 返回那一刻 — 否则 SIGTERM SDK 子进程
    # turn 卡死 (Spike 笔记 docs/spikes/01-02-infra.md "evict 时机" 段).
    pending_rebuild: bool = False
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
                # 必须 resume=. 判定逻辑: 查 SDK transcript (SQLiteSessionStore 镜像) 是
                # 否真有这个 sid 的 entries — 才是 SDK 视角的"跑过 turn".
                #
                # 之前用 sandbox.exists() 启发判, 太脆: 任何外层 (e.g. /chat/with-attachments
                # 在调 _run_chat_turn 之前 commit_attachment 的 mkdir) 提前建出 sandbox dir,
                # 都会被误判 resume → SDK 起进程 exit 1.
                resume_existing = await self._has_sdk_transcript(session_id)
                entry = await self._build(session_id, mounted_dirs, resume=resume_existing)
                self._registry[session_id] = entry
            elif sorted(entry.mounted_dirs) != sorted(mounted_dirs):
                logger.info(
                    f"LoomPool rebuild session={session_id} mounts {entry.mounted_dirs} -> {mounted_dirs}"
                )
                await entry.pl.__aexit__(None, None, None)
                # rebuild 时保留 hitl_allowlists 跟 approval_mode_ref — 重建动机是挂载
                # 目录变了, 跟用户对工具调用的信任 / 审批模式无关, 不应重置.
                old_allowlists = entry.hitl_allowlists
                old_mode_ref = entry.approval_mode_ref
                entry = await self._build(
                    session_id, mounted_dirs, resume=True,
                    hitl_allowlists=old_allowlists,
                    approval_mode_ref=old_mode_ref,
                )
                self._registry[session_id] = entry
            else:
                entry.last_used = time.monotonic()
        return entry.pl, entry.lock

    async def _has_sdk_transcript(self, session_id: str) -> bool:
        """该 sid 在 SDK transcript (SQLiteSessionStore 镜像) 里有 entries 吗?

        判 "需要 resume= 还是 session_id=" 的真理之源 — 跟 SDK 视角对齐.
        project_key_for_directory 是纯字符串变换, 不要求 sandbox 路径存在.
        """
        sandbox = self._settings.sandbox_dir_for(session_id)
        project_key = project_key_for_directory(str(sandbox))
        store = SQLiteSessionStore()
        entries = await store.load({
            "project_key": project_key,
            "session_id": session_id,
            "subpath": "",
        })
        return bool(entries)

    async def _build(
        self,
        session_id: str,
        mounted_dirs: list[str],
        *,
        resume: bool,
        hitl_allowlists: dict[str, set[str]] | None = None,
        approval_mode_ref: ApprovalModeRef | None = None,
    ) -> _Entry:
        sandbox = self._settings.sandbox_dir_for(session_id)
        sandbox.mkdir(parents=True, exist_ok=True)
        # dict 必须先建好再传给 make_can_use_tool, 让 closure 跟 _Entry 持同一引用 —
        # 之后 router add_hitl_allowed 改 dict 才能立刻被 can_use_tool 读到.
        allowlists = hitl_allowlists if hitl_allowlists is not None else {}
        # approval_mode_ref 同理 — 引用类型, 闭包跟 _Entry 共享. 首次 build 默认
        # "default" 模式 (per-conversation 仅内存语义: 每个新会话从 default 起步,
        # 用户在对话框 picker 切换); rebuild 由 caller 传入旧 ref 保留状态.
        if approval_mode_ref is None:
            approval_mode_ref = ApprovalModeRef("default")

        # weaver: 启动 sync skill symlinks + 注入 mcp_server / agents / skills.
        # weave_* 工具完成时回调 mark_pending_rebuild — closure 持 sid + self.
        weaver_subagents, weaver_skill_names = await assemble_weaver(self._settings)
        weaver_server = build_weaver_mcp_server(
            self._settings,
            mark_rebuild=lambda sid=session_id: self.mark_pending_rebuild(sid),
        )

        pl = PentaLoom(
            agents=self._agents,
            extra_mcp_servers={WEAVER_MCP_SERVER_NAME: weaver_server},
            extra_agents=weaver_subagents,
            extra_skills=weaver_skill_names,
            session_id=None if resume else session_id,
            resume=session_id if resume else None,
            cwd=sandbox,
            add_dirs=list(mounted_dirs),
            can_use_tool=make_can_use_tool(
                session_id,
                allowlists=allowlists,
                approval_mode_ref=approval_mode_ref,
            ),
        )
        await pl.__aenter__()
        # rebuild 时 sid 已在 _registry, size 应取 len(); 首次 build 时 caller 还没插
        # 入 sid, 才需要 +1. 区分: resume=True 走 rebuild 路径.
        projected_size = len(self._registry) if resume else len(self._registry) + 1
        logger.info(
            f"LoomPool built session={session_id} sandbox={sandbox} "
            f"mounts={mounted_dirs} resume={resume} (size={projected_size}) "
            f"approval_mode={approval_mode_ref.value} "
            f"weaver_skills={weaver_skill_names}"
        )
        return _Entry(
            pl=pl,
            mounted_dirs=list(mounted_dirs),
            hitl_allowlists=allowlists,
            approval_mode_ref=approval_mode_ref,
        )

    def set_approval_mode(self, session_id: str, mode: str) -> bool:
        """切换会话审批模式. 改 ref.value 立刻被 closure 读到, 不 rebuild.

        语义: 已经在 await fut 等审的请求不受影响 (用户必须答完); 之后进的
        工具调用走新模式. 返回 True = 成功; False = mode 不合法或 session 不存在.
        """
        if mode not in APPROVAL_MODES:
            logger.warning(f"set_approval_mode: invalid mode {mode!r}, ignored")
            return False
        entry = self._registry.get(session_id)
        if entry is None:
            return False
        entry.approval_mode_ref.value = mode
        logger.info(f"approval_mode changed sid={session_id} mode={mode}")
        return True

    def get_approval_mode(self, session_id: str) -> str | None:
        """读会话当前审批模式. 用于前端 picker 初始化 / 刷新页面."""
        entry = self._registry.get(session_id)
        if entry is None:
            return None
        return entry.approval_mode_ref.value

    def add_hitl_allowed(self, session_id: str, tool_name: str, key: str) -> bool:
        """会话级 HITL 免审表加一条. 给 chat_permission router 走 allow_session 时调.

        key 由 tools.workspace.allowlist_key 算出, 调用方负责. 这里不感知工具语义,
        只做 dict[tool_name].add(key).

        返回 True 表示新加, False 表示 session 不存在 (一般是 evict 后才会出现 —
        理论上不可能, 因为 pending future 是 evict 时被 deny 的).
        """
        entry = self._registry.get(session_id)
        if entry is None:
            return False
        entry.hitl_allowlists.setdefault(tool_name, set()).add(key)
        return True

    def mark_pending_rebuild(self, session_id: str) -> bool:
        """weaver 工具完成时调. 当前 turn 不动 client, stream_end 后 chat router 调 evict.

        没什么并发风险: 工具是 in-process, mark flag 是同步 dict 读写.
        """
        entry = self._registry.get(session_id)
        if entry is None:
            return False
        entry.pending_rebuild = True
        return True

    def peek_entry(self, session_id: str) -> _Entry | None:
        """给 chat router stream_end hook 读 pending_rebuild flag 用. 不更新 last_used."""
        return self._registry.get(session_id)

    async def evict(self, session_id: str) -> None:
        entry = self._registry.pop(session_id, None)
        if entry is None:
            return
        # 先清掉该 session 所有 pending 授权 (set Future as deny),
        # 防止 can_use_tool 协程永远 await; 再关 client.
        PERMISSION_REGISTRY.cleanup_session(session_id)
        # StreamBuffer 也清掉 — 子进程没了, 后台 task 跑的 pl.query 会拿到管道
        # 异常, 不 cancel 反而会卡; 顺便释放订阅者.
        stream_buffers.remove(session_id)
        # browser-use CLI 后台 Chrome 进程跟 PentaLoom 子进程是两条独立生命周期,
        # 不清 evict 后浏览器残留. best-effort, 失败吞.
        # 延迟 import 防循环 (tools 模块在启动时间链上).
        from pentaloom.tools.browser import close_browser_use_session

        await close_browser_use_session(self._settings, session_id)
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
