"""TriggerRegistry — schedule + watch 统一管理 (M16 Phase E).

设计照 service_registry.py:75-173 抄: in-memory singleton + asyncio.Lock 串行
化 reload/stop 操作. 区别: trigger 必须 server startup bootstrap 时全部装载
(否则 schedule 错过 cron 时间窗口), service 是 lazy spawn.

公开 API:
  - bootstrap(settings)         server lifespan startup 调, 扫所有 ready app 装 trigger
  - reload_app(settings, name)  finalize_app 成功后调, 先 stop_for_app 清旧再装
  - stop_for_app(name)          finalize_app 失败 / delete_app_soft 调
  - stop_all()                  lifespan shutdown 调
  - list_for_app(name)          modal /triggers endpoint 用, 返 snapshot

共享 _fire_invocation() 函数: trigger fire → invoke_app → catch InvokeError →
race case 写 skipped log + warn / 其他失败仅 warn (invoke_app 内部已写 failed log).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from loguru import logger

from pentaloom.capabilities.weaver import (
    app as app_biz,
    app_runtime,
    index,
)
from pentaloom.capabilities.weaver.triggers.scheduler import ScheduleTrigger
from pentaloom.capabilities.weaver.triggers.watcher import WatchTrigger
from pentaloom.config import Settings


async def _fire_invocation(
    settings: Settings,
    app_name: str,
    invocation_id: str,
    args: dict,
    trigger_kind: str,
) -> None:
    """Trigger 公用 fire 函数. 调 invoke_app, race / overlap case 写 skipped log."""
    try:
        await app_runtime.invoke_app(
            settings,
            app_name=app_name,
            invocation_id=invocation_id,
            args=args,
            trigger=trigger_kind,
        )
        logger.info(
            f"trigger({trigger_kind}) success: {app_name}/{invocation_id}"
        )
    except app_runtime.InvokeError as e:
        msg = str(e)
        # status race: trigger 注册时 ready, 触发瞬间被改成 dirty/failed.
        # invoke_app 抛 "status=... 不能 invoke" — 写 skipped 让用户在 modal 看到.
        if "status=" in msg and "不能 invoke" in msg:
            try:
                app_runtime._append_run_log(
                    settings,
                    app_name,
                    _new_run_id(),
                    invocation_id,
                    status="skipped",
                    duration_ms=0,
                    error=f"trigger skipped (app not ready): {msg[:200]}",
                    trigger=trigger_kind,
                )
            except Exception as log_err:  # noqa: BLE001
                logger.warning(f"trigger skipped log write failed: {log_err}")
            logger.warning(
                f"trigger({trigger_kind}) skipped (app not ready): "
                f"{app_name}/{invocation_id}"
            )
        else:
            # 业务失败 — invoke_app 内部已写 failed 日志, 这里不重复写
            logger.warning(
                f"trigger({trigger_kind}) invoke failed: "
                f"{app_name}/{invocation_id} — {msg[:200]}"
            )


def _new_run_id() -> str:
    """跟 app_runtime._new_run_id 一致格式, 但不引内部以防循环 import."""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"


class TriggerRegistry:
    """app_name → {schedules: [...], watches: [...]} singleton."""

    _instance: "TriggerRegistry | None" = None

    def __init__(self) -> None:
        self._schedules: dict[str, list[ScheduleTrigger]] = {}
        self._watches: dict[str, list[WatchTrigger]] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def instance(cls) -> "TriggerRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ─── lifecycle ─────────────────────────────────────────────

    async def bootstrap(self, settings: Settings) -> dict[str, int]:
        """server lifespan startup 调. 扫所有 ready app 装 trigger.

        必须在 startup 装, 否则 schedule 错过 cron 时间窗口 (例如 server 9:00:01
        启动, schedule 9:00 cron 已经过期).
        """
        self._loop = asyncio.get_running_loop()
        sched_n = 0
        watch_n = 0
        try:
            idx = index.load_index(settings)
            entries = idx.apps
        except Exception as e:  # noqa: BLE001
            logger.warning(f"trigger bootstrap: load_index failed — {e}")
            return {"apps_scanned": 0, "schedules": 0, "watches": 0}
        for entry in entries:
            try:
                r = await self.reload_app(settings, entry.name)
                sched_n += r.get("schedules", 0)
                watch_n += r.get("watches", 0)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"trigger bootstrap: app {entry.name!r} reload failed — {e}"
                )
        logger.info(
            f"trigger bootstrap done: {len(entries)} apps scanned, "
            f"{sched_n} schedule(s), {watch_n} watch(es) started"
        )
        return {
            "apps_scanned": len(entries),
            "schedules": sched_n,
            "watches": watch_n,
        }

    async def reload_app(self, settings: Settings, app_name: str) -> dict[str, int]:
        """finalize_app 成功后调. 先 stop_for_app 清旧 (idempotent), 再读 app.json
        重新装. 返 {schedules, watches} 装了几个 (status != ready 都返 0)."""
        await self.stop_for_app(app_name)

        meta = app_biz.read_meta(settings, app_name)
        if meta is None or meta.status != "ready":
            return {"schedules": 0, "watches": 0, "reason": "not ready"}

        app_def = app_biz.read_app_definition(settings, app_name)
        if app_def is None:
            return {"schedules": 0, "watches": 0, "reason": "no app.json"}

        # 给当前 loop 引用给 WatchTrigger 用 (watchdog 线程 → asyncio 投递)
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("trigger reload: no running loop, skip")
                return {"schedules": 0, "watches": 0, "reason": "no loop"}

        async with self._lock:
            sched_started = 0
            for spec in app_def.components.schedules:
                trig = ScheduleTrigger(
                    settings=settings,
                    app_name=app_name,
                    spec=spec,
                    fire=_fire_invocation,
                )
                try:
                    await trig.start()
                    self._schedules.setdefault(app_name, []).append(trig)
                    sched_started += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"trigger reload: schedule {app_name}/{spec.name} "
                        f"start failed — {e}"
                    )

            watch_started = 0
            for spec in app_def.components.watches:
                # invocation_id=None 是 PR #17 只浏览模式, 不起 watcher 省资源
                if spec.invocation_id is None:
                    continue
                trig = WatchTrigger(
                    settings=settings,
                    app_name=app_name,
                    spec=spec,
                    loop=self._loop,
                    fire=_fire_invocation,
                )
                try:
                    await trig.start()
                    self._watches.setdefault(app_name, []).append(trig)
                    watch_started += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"trigger reload: watch {app_name}/{spec.name} "
                        f"start failed — {e}"
                    )

        logger.info(
            f"trigger reload: {app_name} — {sched_started} schedule(s), "
            f"{watch_started} watch(es)"
        )
        return {"schedules": sched_started, "watches": watch_started}

    async def stop_for_app(self, app_name: str) -> int:
        """delete_app_soft / finalize 失败 / reload 前清旧调. 返停掉的 trigger 总数."""
        n = 0
        async with self._lock:
            for trig in self._schedules.pop(app_name, []):
                try:
                    await trig.stop()
                    n += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"trigger stop: schedule {app_name}/{trig.spec.name} "
                        f"failed — {e}"
                    )
            for trig in self._watches.pop(app_name, []):
                try:
                    await trig.stop()
                    n += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"trigger stop: watch {app_name}/{trig.spec.name} "
                        f"failed — {e}"
                    )
        if n > 0:
            logger.info(f"trigger stop: {app_name} — {n} trigger(s) stopped")
        return n

    async def stop_all(self) -> int:
        """lifespan shutdown 调. 必须在 service_registry.stop_all 之前 (先停 trigger
        防新触发, 再停 service 防孤儿)."""
        n = 0
        app_names = list(set(list(self._schedules.keys()) + list(self._watches.keys())))
        for an in app_names:
            n += await self.stop_for_app(an)
        return n

    # ─── snapshot for UI ───────────────────────────────────────

    def list_for_app(self, app_name: str) -> dict[str, list[dict]]:
        """modal /triggers endpoint 用. 不持锁, 只读 snapshot."""
        schedules = [t.snapshot() for t in (self._schedules.get(app_name) or [])]
        watches = [t.snapshot() for t in (self._watches.get(app_name) or [])]
        return {"schedules": schedules, "watches": watches}


def trigger_registry() -> TriggerRegistry:
    return TriggerRegistry.instance()
