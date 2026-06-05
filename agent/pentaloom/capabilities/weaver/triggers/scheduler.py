"""ScheduleTrigger — cron 驱动 invocation (M16 Phase E).

设计:
  - 每个 schedule 一个 ScheduleTrigger 实例, 内部一个 asyncio.Task 跑 cron 循环
  - 循环: croniter 算下次触发 → asyncio.sleep 等到点 → fire
  - in-flight bool 防 overlap (上次还在跑这次跳过 + 写 skipped log)
  - stop 调 task.cancel() — 不要等当前 invoke 完成 (用户删 app 应即时清掉)

不持久化触发历史 (runs.jsonl 已经记). server restart 期间漏触发不补 (catchup
策略复杂度爆炸, MVP best-effort).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from typing import Any, Callable

from croniter import croniter
from loguru import logger

from pentaloom.capabilities.weaver import app_runtime
from pentaloom.capabilities.weaver.models import AppScheduleSpec
from pentaloom.config import Settings


class ScheduleTrigger:
    """单个 schedule 的运行态. fire 是 registry._fire_invocation."""

    def __init__(
        self,
        *,
        settings: Settings,
        app_name: str,
        spec: AppScheduleSpec,
        fire: Callable[..., Any],
    ) -> None:
        self.settings = settings
        self.app_name = app_name
        self.spec = spec
        self._fire = fire
        self._task: asyncio.Task | None = None
        self._in_flight = False
        self._last_fired_at: float | None = None
        self._next_fire_at: float | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run_loop(), name=f"sched:{self.app_name}:{self.spec.name}"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        while True:
            try:
                now = datetime.now()
                itr = croniter(self.spec.schedule, now)
                next_dt = itr.get_next(datetime)
                wait_s = max(0.0, (next_dt - now).total_seconds())
                self._next_fire_at = next_dt.timestamp()
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"schedule {self.app_name}/{self.spec.name} cron 计算失败 — {e}; "
                    f"backoff 60s 重试"
                )
                await asyncio.sleep(60)
                continue

            try:
                await asyncio.sleep(wait_s)
            except asyncio.CancelledError:
                raise

            if self._in_flight:
                # overlap: 上次还在跑, 跳过这次 + 写 skipped log
                try:
                    app_runtime._append_run_log(
                        self.settings,
                        self.app_name,
                        _new_run_id(),
                        self.spec.invocation_id,
                        status="skipped",
                        duration_ms=0,
                        error="previous invocation still running (schedule overlap)",
                        trigger="schedule",
                    )
                except Exception as log_err:  # noqa: BLE001
                    logger.warning(
                        f"schedule skipped log failed: {log_err}"
                    )
                logger.info(
                    f"schedule skip overlap: {self.app_name}/{self.spec.name}"
                )
                continue

            self._in_flight = True
            self._last_fired_at = datetime.utcnow().timestamp()
            try:
                await self._fire(
                    self.settings,
                    self.app_name,
                    self.spec.invocation_id,
                    dict(self.spec.args),
                    "schedule",
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                # _fire 内部已 catch + log, 这层 fallback
                logger.warning(
                    f"schedule fire unexpected error: "
                    f"{self.app_name}/{self.spec.name} — {e}"
                )
            finally:
                self._in_flight = False

    def snapshot(self) -> dict:
        return {
            "name": self.spec.name,
            "schedule": self.spec.schedule,
            "invocation_id": self.spec.invocation_id,
            "next_fire_at": self._next_fire_at,
            "last_fired_at": self._last_fired_at,
            "in_flight": self._in_flight,
        }


def _new_run_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"
