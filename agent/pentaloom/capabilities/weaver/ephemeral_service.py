"""Ephemeral service registry — 织造期临时 service 跑法 (memory-only).

跟 declared service (finalize 后 launchd plist 监督) 双轨并存:

  declared  | finalize 后 launchd KeepAlive 监督, 跨 PentaLoom 重启仍活. 生产形态.
  ephemeral | agent 织造期 weave_service_start 起的 subprocess. PentaLoom agent
              进程持句柄, PentaLoom 重启全清. 调试 / 测 service 真起得来用.

互斥语义:
  - ephemeral / declared 写同一份 .runtime/<svc>.port — _invoke_service 不区分
  - 同名 declared 已 ready 时 ephemeral start 走 stop 旧的再起新的 (idempotent)
  - finalize_app 触发 reload_for_app 前会调 stop_all_for_app, 让 launchd 接管

不持久:
  - 进程句柄存 module-level dict, PentaLoom 重启丢光
  - log 在 sandbox/.ephemeral-logs/<svc>.log 落盘 (跨重启可看)
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pentaloom.capabilities.weaver import app as app_biz
from pentaloom.capabilities.weaver import paths
from pentaloom.capabilities.weaver.app_env import weaver_app_env
from pentaloom.config import Settings
from pentaloom.infra import python_env

logger = logging.getLogger(__name__)


class EphemeralError(Exception):
    """ephemeral 启停 / 查询失败统称."""


@dataclass
class EphemeralService:
    app_name: str
    service_name: str
    pid: int
    port: int
    started_at: float
    log_path: Path
    process: asyncio.subprocess.Process = field(repr=False)
    _stdout_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _stderr_task: Optional[asyncio.Task] = field(default=None, repr=False)

    @property
    def uptime_s(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def is_alive(self) -> bool:
        return self.process.returncode is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "app_name": self.app_name,
            "service_name": self.service_name,
            "pid": self.pid,
            "port": self.port,
            "started_at": self.started_at,
            "uptime_s": round(self.uptime_s, 1),
            "log_path": str(self.log_path),
            "alive": self.is_alive(),
            "exit_code": self.process.returncode,
        }


class EphemeralServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[tuple[str, str], EphemeralService] = {}
        self._lock = asyncio.Lock()

    def get(self, app_name: str, service_name: str) -> Optional[EphemeralService]:
        return self._services.get((app_name, service_name))

    def list(self) -> list[EphemeralService]:
        # 清掉已死的 — UI / agent 看不到僵尸条目
        dead = [k for k, s in self._services.items() if not s.is_alive()]
        for k in dead:
            self._services.pop(k, None)
        return list(self._services.values())

    def list_for_app(self, app_name: str) -> list[EphemeralService]:
        return [s for s in self.list() if s.app_name == app_name]

    async def start(
        self, settings: Settings, app_name: str, service_name: str,
    ) -> EphemeralService:
        """启 ephemeral subprocess. 同 (app, svc) 已在 → 先 stop 再 start (idempotent).

        Raises:
          EphemeralError: app/service 不存在 / deps install 失败 / spawn 失败 /
            ready probe 超时 (5s 内没起 listen).
        """
        async with self._lock:
            existing = self._services.get((app_name, service_name))
            if existing is not None and existing.is_alive():
                logger.info(
                    f"ephemeral.start({app_name}/{service_name}): 已在跑 pid={existing.pid}, "
                    f"先 stop 再 start (idempotent)"
                )
                await self._stop_locked(app_name, service_name)

            spec, files_root, cwd = self._resolve_spec(settings, app_name, service_name)

            if spec.python_deps:
                logger.info(
                    f"ephemeral.start({app_name}/{service_name}): uv add {spec.python_deps}"
                )
                result = await python_env.install_libs(settings, list(spec.python_deps))
                if result.exit_code != 0:
                    raise EphemeralError(
                        f"python_deps install 失败 (uv add exit={result.exit_code}): "
                        f"{result.stderr[:300] or result.stdout[:300]}"
                    )

            if spec.port:
                # 固定端口: 预探测 — 被占了立刻给清晰错, 别等 spawn 后看 log.
                port = spec.port
                if not _port_available(port):
                    raise EphemeralError(
                        f"端口 {port} 被占用 (app.json components.services[].port "
                        f"写死的固定端口). 改 app.json 把 service {service_name!r} 的 "
                        f"port 字段换成另一个 (≥9000 推荐), 用 weave_app_revise 改后重试. "
                        f"被谁占: lsof -nP -iTCP:{port} -sTCP:LISTEN"
                    )
            else:
                port = _pick_free_port()
            env = python_env.build_env(settings)
            env.update(weaver_app_env(
                settings, app_name,
                service_name=spec.name, service_port=port,
            ))

            command = list(spec.command)
            if command and command[0] == "python":
                uv = python_env.uv_bin(env)
                command = [
                    uv, "run", "--project", str(settings.python_env_dir), *command
                ]

            log_dir = paths.app_dir(settings, app_name) / ".ephemeral-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{service_name}.log"

            # 端口文件先写 — _invoke_service 读它, ready probe 之后才生效
            _write_port_file(settings, app_name, service_name, port)

            logger.info(
                f"ephemeral.start({app_name}/{service_name}): spawn "
                f"port={port} cwd={cwd} cmd={' '.join(command)}"
            )

            try:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(cwd),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,  # detach 从 PentaLoom session, ctrl-c PentaLoom 不连带 kill
                )
            except (OSError, FileNotFoundError) as e:
                _clear_port_file(settings, app_name, service_name)
                raise EphemeralError(
                    f"subprocess spawn 失败 ({command[0]!r}): {e}. "
                    f"command[0] 找得到吗? cwd 存在吗?"
                ) from e

            # log file truncate (新一轮 start 不混老 log)
            log_path.write_text("")
            stdout_task = asyncio.create_task(
                _pipe_to_log(proc.stdout, log_path, "stdout"),
                name=f"eph-stdout:{app_name}/{service_name}",
            )
            stderr_task = asyncio.create_task(
                _pipe_to_log(proc.stderr, log_path, "stderr"),
                name=f"eph-stderr:{app_name}/{service_name}",
            )

            svc = EphemeralService(
                app_name=app_name,
                service_name=service_name,
                pid=proc.pid,
                port=port,
                started_at=time.time(),
                log_path=log_path,
                process=proc,
                _stdout_task=stdout_task,
                _stderr_task=stderr_task,
            )
            self._services[(app_name, service_name)] = svc

            # ready probe: spec.startup_timeout_ms 内 TCP 能连上 127.0.0.1:port 算起来了
            timeout_s = max(0.1, spec.startup_timeout_ms / 1000.0)
            ok = await _wait_listen("127.0.0.1", port, timeout_s=timeout_s)
            if not ok:
                tail = await _read_log_tail(log_path, 40)
                if proc.returncode is not None:
                    # 已经死了, 收尸 + 给针对性诊断
                    self._services.pop((app_name, service_name), None)
                    _clear_port_file(settings, app_name, service_name)
                    diag = _diagnose_python_service_exit(spec, cwd, tail)
                    raise EphemeralError(
                        f"service spawn 后立刻退出 (exit={proc.returncode}). "
                        f"{diag}\n日志末尾:\n{tail}"
                    )
                # weave_service_start 是 verify 工具 — timeout 仍在跑也算 verify 失败,
                # stop + clear + raise, 别静默放过. 慢启动 service 调大 startup_timeout_ms.
                logger.warning(
                    f"ephemeral.start({app_name}/{service_name}): "
                    f"{timeout_s:.1f}s 内未 listen 127.0.0.1:{port}, stop + raise"
                )
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                for t in (stdout_task, stderr_task):
                    if t and not t.done():
                        t.cancel()
                self._services.pop((app_name, service_name), None)
                _clear_port_file(settings, app_name, service_name)
                diag = _diagnose_python_service_exit(spec, cwd, tail)
                raise EphemeralError(
                    f"service {timeout_s:.1f}s 内未 listen 127.0.0.1:{port} "
                    f"(startup_timeout_ms={spec.startup_timeout_ms}). "
                    f"{diag}\n日志末尾:\n{tail}\n"
                    f"调大 spec.startup_timeout_ms 或检查 service 是否真 listen 127.0.0.1:{port}."
                )

            return svc

    async def stop(self, app_name: str, service_name: str) -> bool:
        """停 ephemeral service. 不存在 / 已死返 False, 真停了返 True."""
        async with self._lock:
            return await self._stop_locked(app_name, service_name)

    async def _stop_locked(self, app_name: str, service_name: str) -> bool:
        svc = self._services.get((app_name, service_name))
        if svc is None:
            return False
        if svc.is_alive():
            logger.info(
                f"ephemeral.stop({app_name}/{service_name}): SIGTERM pid={svc.pid}"
            )
            try:
                svc.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(svc.process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"ephemeral.stop({app_name}/{service_name}): SIGTERM 3s 没退, SIGKILL pid={svc.pid}"
                )
                try:
                    svc.process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(svc.process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.error(
                        f"ephemeral.stop({app_name}/{service_name}): SIGKILL 2s 也没退, 放弃"
                    )

        # cancel pipe tasks
        for t in (svc._stdout_task, svc._stderr_task):
            if t and not t.done():
                t.cancel()

        self._services.pop((app_name, service_name), None)
        # 删 port file: declared 接管时不留 stale 端口
        from pentaloom.config import get_settings
        _clear_port_file(get_settings(), app_name, service_name)
        return True

    async def stop_all_for_app(self, app_name: str) -> int:
        """app 名下所有 ephemeral service 都 stop. finalize_app 前调."""
        async with self._lock:
            keys = [k for k in self._services.keys() if k[0] == app_name]
            for k in keys:
                await self._stop_locked(*k)
            return len(keys)

    async def stop_all(self) -> int:
        """server 关停时清光. lifespan 钩子调."""
        async with self._lock:
            keys = list(self._services.keys())
            for k in keys:
                await self._stop_locked(*k)
            return len(keys)

    async def tail_logs(
        self, app_name: str, service_name: str, *, n: int = 50,
    ) -> dict[str, Any]:
        """读 ephemeral service log 末尾 n 行. 不存在 → 抛."""
        svc = self._services.get((app_name, service_name))
        log_path: Optional[Path] = svc.log_path if svc else None
        # 即便 svc 已死, log 还能读 (落盘了)
        if log_path is None:
            from pentaloom.config import get_settings
            settings = get_settings()
            candidate = paths.app_dir(settings, app_name) / ".ephemeral-logs" / f"{service_name}.log"
            if not candidate.exists():
                raise EphemeralError(
                    f"ephemeral service {app_name}/{service_name} 没起过 ({candidate} 不存在)"
                )
            log_path = candidate

        tail = await _read_log_tail(log_path, n)
        return {
            "app_name": app_name,
            "service_name": service_name,
            "log_path": str(log_path),
            "lines": tail.splitlines(),
            "alive": svc.is_alive() if svc else False,
            "pid": svc.pid if svc else None,
            "port": svc.port if svc else None,
        }

    def _resolve_spec(self, settings: Settings, app_name: str, service_name: str):
        """读 app.json 拿 service spec + 校 cwd (files/ 内)."""
        app_def = app_biz.read_app_definition(settings, app_name)
        if app_def is None:
            raise EphemeralError(f"app {app_name!r} 不存在 (app.json missing)")
        spec = next(
            (s for s in app_def.components.services if s.name == service_name), None
        )
        if spec is None:
            raise EphemeralError(
                f"service {service_name!r} 不在 {app_name}/app.json components.services"
            )

        files_root = paths.app_files_dir(settings, app_name)
        if spec.workdir:
            cwd = (files_root / spec.workdir).resolve()
            try:
                cwd.relative_to(files_root.resolve())
            except ValueError as e:
                raise EphemeralError(
                    f"workdir {spec.workdir!r} 越出 files/ 根"
                ) from e
            if not cwd.is_dir():
                raise EphemeralError(
                    f"workdir {spec.workdir!r} 不是目录: {cwd}"
                )
        else:
            cwd = files_root
        return spec, files_root, cwd


# ────────────────────────────────────────────────────────────────────
# module-level helpers + singleton
# ────────────────────────────────────────────────────────────────────


_registry: Optional[EphemeralServiceRegistry] = None


def get_registry() -> EphemeralServiceRegistry:
    global _registry
    if _registry is None:
        _registry = EphemeralServiceRegistry()
    return _registry


def _pick_free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _port_available(port: int) -> bool:
    """探测 127.0.0.1:port 能不能 bind. 能 = 没人占, 立刻 close 还给 OS."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        s.close()
    return True


def _write_port_file(
    settings: Settings, app_name: str, comp_name: str, port: int,
) -> None:
    runtime_dir = paths.app_dir(settings, app_name) / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / f"{comp_name}.port").write_text(str(port))


def _clear_port_file(settings: Settings, app_name: str, comp_name: str) -> None:
    p = paths.app_dir(settings, app_name) / ".runtime" / f"{comp_name}.port"
    try:
        p.unlink()
    except FileNotFoundError:
        pass


async def _wait_listen(host: str, port: int, *, timeout_s: float) -> bool:
    """轮询 TCP connect, 成功一次即 True."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r, w = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=0.3
            )
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
            return True
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.2)
    return False


async def _pipe_to_log(
    stream: Optional[asyncio.StreamReader], log_path: Path, tag: str,
) -> None:
    """异步把 stdout/stderr 行追加到 log file (带 [tag] 前缀)."""
    if stream is None:
        return
    try:
        while True:
            line = await stream.readline()
            if not line:
                return
            try:
                text = line.decode("utf-8", errors="replace").rstrip("\n")
            except Exception:
                text = repr(line)
            try:
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"[{tag}] {text}\n")
            except OSError:
                # log 写不了不致命 — 继续 drain stream 避免 pipe 阻塞 service
                pass
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.warning(f"_pipe_to_log({tag}): {e}")


# Python web server 长期运行入口 — service 跑起来必须命中这三个之一才算
# 真正 listen, 否则就是 "只定义 app 没启动" 的 exit=0 经典坑.
_PYTHON_SERVE_CALLS = ("uvicorn.run(", "app.run(", "web.run_app(")


def _diagnose_python_service_exit(spec, cwd: Path, log_tail: str) -> str:
    """spec.command 是 Python 入口时, 给针对性 hint. 否则空串.

    当前两类常见错:
      1. 文件只有 app=FastAPI() — 跑 python file.py 立刻 exit=0 (没 uvicorn.run)
      2. ModuleNotFoundError — python_deps 漏了
    """
    cmd = list(spec.command)
    if not cmd or cmd[0] not in {"python", "python3"} or len(cmd) < 2:
        return ""
    if cmd[1].startswith("-"):
        return ""  # python -m / -c 跳

    # log tail 优先 — ModuleNotFoundError 比静态扫更可信
    if "ModuleNotFoundError" in log_tail or "ImportError" in log_tail:
        return (
            "诊断: 日志含 ModuleNotFoundError/ImportError — service 缺依赖. "
            "用 weave_app_revise 把缺的模块加进 app.json components.services[].python_deps."
        )

    entry_path = (cwd / cmd[1]).resolve()
    try:
        entry_path.relative_to(cwd.resolve())
    except ValueError:
        return ""
    if not entry_path.is_file():
        return ""
    try:
        text = entry_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if any(call in text for call in _PYTHON_SERVE_CALLS):
        return ""  # 入口看起来对, 别瞎猜 — 让 log tail 自己说话

    return (
        f"诊断: {cmd[1]} 没找到 uvicorn.run( / app.run( / web.run_app( — "
        f"FastAPI service 通常少了启动段. 加:\n"
        f'  if __name__ == "__main__":\n'
        f"      import os, uvicorn\n"
        f'      uvicorn.run(app, host="127.0.0.1", '
        f'port=int(os.environ["PENTALOOM_APP_PORT"]))'
    )


async def _read_log_tail(log_path: Path, n: int) -> str:
    """读末尾 n 行. log 一般小 (几十 KB), splitlines OK."""
    if not log_path.exists():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()[-max(1, n):]
    return "\n".join(lines)
