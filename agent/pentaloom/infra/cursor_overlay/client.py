"""cursor_overlay 主进程 client. helper 子进程通过 stdin/stdout 通信. 失败永不抛."""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class OverlayClient:
    process: asyncio.subprocess.Process
    pid: int
    _dead: bool = field(default=False)
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def alive(self) -> bool:
        return not self._dead and self.process.returncode is None

    async def send(self, op: dict) -> bool:
        """fire-and-forget. helper 死了返 False, 调用方静默 skip."""
        if not self.alive:
            return False
        line = json.dumps(op, ensure_ascii=False) + "\n"
        try:
            async with self._send_lock:
                if self.process.stdin is None:
                    return False
                self.process.stdin.write(line.encode("utf-8"))
                await self.process.stdin.drain()
            return True
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            logger.warning(f"cursor_overlay helper send failed ({e!r}); marking dead")
            self._dead = True
            return False

    async def shutdown(self) -> None:
        """优雅关 helper: 发 shutdown 命令 → 等 1s 自退 → SIGTERM → SIGKILL. 幂等."""
        if self._dead and self.process.returncode is not None:
            return
        self._dead = True
        try:
            line = b'{"op": "shutdown"}\n'
            if self.process.stdin is not None:
                self.process.stdin.write(line)
                await self.process.stdin.drain()
                self.process.stdin.close()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            pass
        try:
            await asyncio.wait_for(self.process.wait(), timeout=1.0)
            logger.info(f"cursor_overlay helper exited cleanly (pid={self.pid}, rc={self.process.returncode})")
            return
        except asyncio.TimeoutError:
            pass
        try:
            self.process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self.process.wait(), timeout=1.0)
            logger.info(f"cursor_overlay helper SIGTERM (pid={self.pid})")
            return
        except asyncio.TimeoutError:
            logger.warning(f"cursor_overlay helper SIGTERM 超时, SIGKILL pid={self.pid}")
        try:
            self.process.kill()
            await self.process.wait()
        except ProcessLookupError:
            pass


async def start_helper(*, timeout_s: float = 5.0) -> OverlayClient | None:
    """spawn helper + 等 READY 握手. 超时 / 失败返 None (overlay 禁用, 主功能继续)."""
    cmd = [sys.executable, "-u", "-m", "pentaloom.infra.cursor_overlay.helper"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"cursor_overlay helper spawn 失败: {e!r}")
        return None

    if proc.stdout is None:
        logger.warning("cursor_overlay helper stdout 不可读")
        return None

    try:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning(f"cursor_overlay helper {timeout_s}s 超时无 READY, 禁用")
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return None

    if line.strip() != b"READY":
        logger.warning(f"cursor_overlay helper 握手失败 ({line!r}), 禁用")
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return None

    logger.info(f"cursor_overlay helper started pid={proc.pid}")
    return OverlayClient(process=proc, pid=proc.pid)
