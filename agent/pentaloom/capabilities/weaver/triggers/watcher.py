"""WatchTrigger — fs event 驱动 invocation (M16 Phase E).

watchdog Observer 跑独立线程 → 事件 callback 用 asyncio.run_coroutine_threadsafe
投回 main loop → debounce 合并 burst → fire invocation.

设计:
  - 每个 watch 一个 WatchTrigger 实例 (一个 Observer + 一个 handler)
  - watch.invocation_id=None 不实例化此类 (registry 跳过), 仅 PR #17 UI 浏览模式
  - debounce_ms 默认 300ms: 一次保存 burst 合并成 1 次触发 (vscode/nodemon 同款)
  - _pending_events cap 100 防 args 体积爆炸; 超出 truncated=true
  - 默认非递归 (recursive=False), 大目录 1000+ 文件递归会拖 Observer

跨平台限制: macOS FSEvents 有 100ms+ 延迟 (非 sub-100ms 实时), Linux inotify
即时. Windows / NFS / 网络挂载行为差异不保证.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from pentaloom.capabilities.weaver import app_runtime, paths
from pentaloom.capabilities.weaver.models import AppWatchSpec
from pentaloom.config import Settings

_EVENT_CAP = 100  # 单次触发 args.events 最多 100 个 entry, 超出 truncated=true


# watchdog event_type → 我们 schema 的 WatchEvent
_EVENT_MAP = {
    "modified": "modify",
    "created": "create",
    "deleted": "delete",
    "moved": "move",
}


class WatchTrigger:
    """单个 watch 的运行态. fire 是 registry._fire_invocation."""

    def __init__(
        self,
        *,
        settings: Settings,
        app_name: str,
        spec: AppWatchSpec,
        loop: asyncio.AbstractEventLoop,
        fire: Callable[..., Any],
    ) -> None:
        assert spec.invocation_id is not None, "WatchTrigger 不应实例化无 invocation_id 的 spec"
        self.settings = settings
        self.app_name = app_name
        self.spec = spec
        self._loop = loop
        self._fire = fire
        self._observer: Observer | None = None
        self._debounce_task: asyncio.Task | None = None
        self._pending_events: list[dict] = []
        self._pending_truncated = False
        self._in_flight = False
        self._last_fired_at: float | None = None
        self._last_event_at: float | None = None

    async def start(self) -> None:
        if self._observer is not None:
            return

        # 解析监听目录, 校越界 (复用 app._resolve_within_files 思路)
        app_root = paths.app_dir(self.settings, self.app_name)
        target = (app_root / "files" / self.spec.path).resolve()
        try:
            target.relative_to(app_root.resolve())
        except ValueError as e:
            raise RuntimeError(
                f"watch.path {self.spec.path!r} 越出 app 目录"
            ) from e
        target.mkdir(parents=True, exist_ok=True)  # 不存在自动建, 否则 Observer schedule 会 raise

        handler = _Handler(self, set(self.spec.events))
        observer = Observer()
        observer.schedule(handler, str(target), recursive=False)
        observer.daemon = True  # main 退出时一起退, 不卡住 server shutdown
        observer.start()
        self._observer = observer
        logger.info(
            f"watch start: {self.app_name}/{self.spec.name} → {target} "
            f"events={self.spec.events} debounce={self.spec.debounce_ms}ms"
        )

    async def stop(self) -> None:
        # 先 cancel debounce timer 不让最后一波触发
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._debounce_task
        if self._observer is not None:
            # observer.stop() + join 同步, 但快 (毫秒级)
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None

    # ─── watchdog 线程 → main loop ────────────────────────────

    def _on_fs_event_threadsafe(self, event: FileSystemEvent) -> None:
        """watchdog 在 Observer 线程调这, 投回 main loop."""
        mapped = _EVENT_MAP.get(event.event_type)
        if mapped is None or mapped not in self.spec.events:
            return
        evt = {"type": mapped, "path": event.src_path}
        # 用 call_soon_threadsafe 而非 run_coroutine_threadsafe — _enqueue 不是 coroutine
        # 主调度仍在 main loop, debounce_task 也起在 main loop
        try:
            self._loop.call_soon_threadsafe(self._enqueue, evt)
        except RuntimeError:
            # loop 关了, drop event
            pass

    def _enqueue(self, evt: dict) -> None:
        self._last_event_at = datetime.utcnow().timestamp()
        if len(self._pending_events) >= _EVENT_CAP:
            self._pending_truncated = True
        else:
            self._pending_events.append(evt)
        # 重置 debounce
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(
            self._debounced_fire(),
            name=f"watch:{self.app_name}:{self.spec.name}:debounce",
        )

    async def _debounced_fire(self) -> None:
        try:
            await asyncio.sleep(self.spec.debounce_ms / 1000)
        except asyncio.CancelledError:
            return

        events = self._pending_events
        truncated = self._pending_truncated
        self._pending_events = []
        self._pending_truncated = False

        if self._in_flight:
            # in-flight 期间又攒了 burst — 写 skipped, 不重排
            try:
                app_runtime._append_run_log(
                    self.settings,
                    self.app_name,
                    _new_run_id(),
                    self.spec.invocation_id,
                    status="skipped",
                    duration_ms=0,
                    error="watch events during in-flight invocation",
                    trigger="watch",
                )
            except Exception as log_err:  # noqa: BLE001
                logger.warning(f"watch skipped log failed: {log_err}")
            return

        self._in_flight = True
        self._last_fired_at = datetime.utcnow().timestamp()
        try:
            args = {
                **self.spec.args,
                "events": events,
            }
            if truncated:
                args["truncated"] = True
            await self._fire(
                self.settings,
                self.app_name,
                self.spec.invocation_id,
                args,
                "watch",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"watch fire unexpected error: "
                f"{self.app_name}/{self.spec.name} — {e}"
            )
        finally:
            self._in_flight = False

    def snapshot(self) -> dict:
        return {
            "name": self.spec.name,
            "path": self.spec.path,
            "events": list(self.spec.events),
            "invocation_id": self.spec.invocation_id,
            "debounce_ms": self.spec.debounce_ms,
            "last_event_at": self._last_event_at,
            "last_fired_at": self._last_fired_at,
            "in_flight": self._in_flight,
        }


class _Handler(FileSystemEventHandler):
    """watchdog event sink. 跑在 Observer 线程, 转发给 WatchTrigger."""

    def __init__(self, trigger: WatchTrigger, events_filter: set[str]) -> None:
        super().__init__()
        self._trigger = trigger
        # events_filter 在 _on_fs_event_threadsafe 那层也再校一遍, 这里是个早 filter
        self._events_filter = events_filter

    def on_any_event(self, event: FileSystemEvent) -> None:
        # 目录事件忽略, 我们只关心文件 (除非 events_filter 含 delete 然后用户删目录,
        # 罕见, 不处理)
        if event.is_directory:
            return
        self._trigger._on_fs_event_threadsafe(event)


def _new_run_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"
