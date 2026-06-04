"""Service runtime registry — Phase D.

每个 weaver app 的 service component 是个 long-running subprocess. agent 调
invoke_app(target=service) → runtime lazy spawn service (找空闲端口, 注入 env, 等
TCP probe ready) → HTTP fetch service endpoint → 返结果.

设计要点:
  - **Lazy spawn**: invoke_app 时才起 service, 不是 finalize 时 (跟 script 一致, 防 10
    个 app 都 finalize 后 10 个 service 常驻吃内存)
  - **Port 动态优先**: app.json port=null → runtime bind 0 拿系统分配, port=<int> →
    写死 (冲突时 spawn 失败抛错)
  - **Env 注入**: PENTALOOM_APP_PORT / APP_NAME / SERVICE_NAME / APP_DIR / FILES_DIR /
    RUNS_DIR — service 进程读这些 env 知道自己该 listen 哪 + 上下文路径
  - **TCP ready probe**: spawn 后循环 connect 端口, 默认 5s timeout (manifest
    startup_timeout_ms 可覆盖). 不要求 service 实现 /health
  - **Restart policy**: never (死了就死) / on_failure (exit code != 0 重启限 5 次).
    always 留后续 phase
  - **Log**: stdout + stderr 合并写 logs/service-<name>.log, 行首加 [stdout]/[stderr] 前缀
  - **Cleanup**: FastAPI lifespan shutdown 调 stop_all(); delete_weaver(kind=app) 调
    stop_for_app() — 防孤儿 process

不持久化, in-memory singleton. agent restart 重 spawn 重新分配端口.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from pentaloom.capabilities.weaver import paths
from pentaloom.capabilities.weaver.models import AppDefinition, AppServiceSpec
from pentaloom.config import Settings
from pentaloom.infra import python_env

RESTART_MAX = 5  # on_failure 重启上限, 防死循环重启把 CPU 烧光
RESTART_BACKOFF_S = 1.0  # 每次重启间隔 (固定, 不退避; D 一期简单)


class ServiceError(Exception):
    """service spawn / probe / 状态相关错误. app_runtime 包成 InvokeError."""


@dataclass
class RunningService:
    """一个跑着的 service 进程的状态."""

    app_name: str
    service_name: str
    port: int
    process: asyncio.subprocess.Process | None
    log_file_path: Path
    log_fp: object  # file object, stop 时 close
    log_task: asyncio.Task | None  # stdout/stderr 转发到 log file 的协程
    spec: AppServiceSpec
    started_at: float = field(default_factory=time.time)
    restart_count: int = 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None


class ServiceRegistry:
    """app_name → {service_name → RunningService} singleton.

    所有方法都是 async (spawn / kill 走 asyncio subprocess). 公开入口:
      - ensure_service(settings, app_name, service_name) → RunningService (lazy spawn)
      - stop_service(app_name, service_name)
      - stop_for_app(app_name) — delete_weaver 时调
      - stop_all() — lifespan shutdown 时调
      - list_for_app(app_name) → 给 inspect_weaver 看 running services
    """

    _instance: "ServiceRegistry | None" = None

    def __init__(self) -> None:
        self._services: dict[str, dict[str, RunningService]] = {}
        self._lock = asyncio.Lock()  # 串行化 spawn, 防并发同 app/service 重复启

    @classmethod
    def instance(cls) -> "ServiceRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ─── 公开入口 ───────────────────────────────────────────────

    async def ensure_service(
        self,
        settings: Settings,
        app_name: str,
        service_name: str,
    ) -> RunningService:
        """Lazy spawn. 已起且活 → 直接返; 已起死了 → 按 restart policy 处理; 没起 → spawn.

        invoke_app(target=service) 唯一入口. 串行化避免并发 invoke 重复 spawn 同 service.
        """
        async with self._lock:
            existing = self._get(app_name, service_name)
            if existing is not None:
                if existing.is_alive:
                    return existing
                # 死了
                if existing.spec.restart == "never":
                    self._forget(app_name, service_name)
                    raise ServiceError(
                        f"service {app_name}/{service_name} 已死且 restart=never"
                    )
                if existing.spec.restart == "on_failure":
                    if existing.restart_count >= RESTART_MAX:
                        self._forget(app_name, service_name)
                        raise ServiceError(
                            f"service {app_name}/{service_name} 重启超过 {RESTART_MAX} 次, 放弃"
                        )
                    # 兜回去 respawn, restart_count + 1
                    spec = existing.spec
                    restart_count = existing.restart_count + 1
                    self._forget(app_name, service_name)
                    await asyncio.sleep(RESTART_BACKOFF_S)
                    rs = await self._spawn(settings, app_name, spec)
                    rs.restart_count = restart_count
                    self._put(app_name, rs)
                    return rs
                # always — D 一期不支持
                raise ServiceError(
                    f"service {app_name}/{service_name} restart={existing.spec.restart!r} "
                    "暂未实装 (always 留后续 phase)"
                )

            # 没起过 — 找 spec spawn
            spec = self._find_spec(settings, app_name, service_name)
            rs = await self._spawn(settings, app_name, spec)
            self._put(app_name, rs)
            return rs

    async def stop_service(self, app_name: str, service_name: str) -> bool:
        """SIGTERM → 等 3s → SIGKILL. 返 True 表示停过, False 表示没起."""
        async with self._lock:
            rs = self._get(app_name, service_name)
            if rs is None:
                return False
            await self._kill(rs)
            self._forget(app_name, service_name)
            return True

    async def stop_for_app(self, app_name: str) -> int:
        """app delete 时调. 返停掉的 service 数."""
        names = list((self._services.get(app_name) or {}).keys())
        n = 0
        for sn in names:
            if await self.stop_service(app_name, sn):
                n += 1
        return n

    async def stop_all(self) -> int:
        """lifespan shutdown 调. 返停掉的 service 数."""
        n = 0
        app_names = list(self._services.keys())
        for an in app_names:
            n += await self.stop_for_app(an)
        return n

    def list_for_app(self, app_name: str) -> list[dict]:
        """inspect_weaver(kind=app) 用 — 不持锁, 只读 snapshot.

        字段对齐 sidebar D-4 显示:
          - name / port / pid: 基本信息
          - status: 'running' | 'dead' (alive bool 对前端展示不直观, 直接字符串)
          - started_at: unix ts (前端 fmt)
          - restart_count: on_failure 重启过几次
          - log_path: tail_weaver_logs(mode='service:<name>') 拿同一份, 也透给前端
            做 hover tooltip
        """
        services = self._services.get(app_name) or {}
        out: list[dict] = []
        for rs in services.values():
            proc = rs.process
            pid = proc.pid if proc is not None else None
            out.append({
                "name": rs.service_name,
                "status": "running" if rs.is_alive else "dead",
                "port": rs.port,
                "pid": pid,
                "started_at": rs.started_at,
                "restart_count": rs.restart_count,
                "log_path": str(rs.log_file_path),
            })
        return out

    # ─── 内部 ────────────────────────────────────────────────────

    def _get(self, app_name: str, service_name: str) -> RunningService | None:
        return (self._services.get(app_name) or {}).get(service_name)

    def _put(self, app_name: str, rs: RunningService) -> None:
        self._services.setdefault(app_name, {})[rs.service_name] = rs

    def _forget(self, app_name: str, service_name: str) -> None:
        app_map = self._services.get(app_name)
        if app_map and service_name in app_map:
            del app_map[service_name]
        if app_map is not None and not app_map:
            del self._services[app_name]

    def _find_spec(
        self, settings: Settings, app_name: str, service_name: str
    ) -> AppServiceSpec:
        from pentaloom.capabilities.weaver import app as app_biz

        app_def: AppDefinition | None = app_biz.read_app_definition(settings, app_name)
        if app_def is None:
            raise ServiceError(
                f"app {app_name!r} 缺 app.json — service 必须先在 app.json.components.services 声明"
            )
        for s in app_def.components.services:
            if s.name == service_name:
                return s
        raise ServiceError(
            f"service {service_name!r} 不在 app {app_name!r} 的 app.json "
            f"(可用: {[s.name for s in app_def.components.services]})"
        )

    async def _spawn(
        self, settings: Settings, app_name: str, spec: AppServiceSpec
    ) -> RunningService:
        """实际 spawn: 端口分配 + python_deps 装 + env 注入 + Popen + log 转发 + TCP ready probe."""
        # 0. python_deps — uv add 装到共享 venv. idempotent (已装的跳过), 首次装 fastapi
        # 之类要十几秒, 之后秒回. install_libs 失败抛, 上层包成 InvokeError 给 agent.
        if spec.python_deps:
            logger.info(
                f"service_registry: ensuring python_deps for {app_name}/{spec.name}: {spec.python_deps}"
            )
            install_result = await python_env.install_libs(settings, list(spec.python_deps))
            if install_result.exit_code != 0:
                raise ServiceError(
                    f"python_deps 装失败 (uv add exit={install_result.exit_code}): "
                    f"{install_result.stderr[:300] or install_result.stdout[:300]}"
                )

        # 1. 端口
        port = spec.port if spec.port else _pick_free_port()

        # 2. cwd
        files_root = paths.app_files_dir(settings, app_name)
        if spec.workdir:
            from pentaloom.capabilities.weaver.app import _resolve_within_files
            from pentaloom.capabilities.weaver import index as weaver_index

            try:
                cwd = _resolve_within_files(files_root, spec.workdir, label=f"service.{spec.name}.workdir")
            except weaver_index.WeaverError as e:
                raise ServiceError(str(e)) from e
        else:
            cwd = files_root

        # 3. env
        env = python_env.build_env(settings)
        env.update({
            "PENTALOOM_APP_PORT": str(port),
            "PENTALOOM_APP_NAME": app_name,
            "PENTALOOM_SERVICE_NAME": spec.name,
            "PENTALOOM_APP_DIR": str(paths.app_dir(settings, app_name)),
            "PENTALOOM_FILES_DIR": str(files_root),
            "PENTALOOM_RUNS_DIR": str(paths.app_runs_dir(settings, app_name)),
        })

        # 4. command — `python` 走 venv 跑, 其他原样 (跟 run_app_script 同款思路)
        command = list(spec.command)
        if command[0] == "python":
            # 借 python_env 的 uv run 模式: uv run --project <env> python <args...>
            uv = python_env.uv_bin(env)
            command = [uv, "run", "--project", str(settings.python_env_dir), *command]

        # 5. log file
        log_path = paths.app_logs_dir(settings, app_name) / f"service-{spec.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # append 模式 — 重启时 history 累积; 用户嫌大自己清
        log_fp = open(log_path, "a", buffering=1, encoding="utf-8")  # line buffered
        log_fp.write(f"\n=== service {app_name}/{spec.name} started at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} port={port} ===\n")

        # 6. spawn
        logger.info(
            f"service_registry: spawn {app_name}/{spec.name} port={port} cwd={cwd} cmd={' '.join(command)}"
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # 独立 process group, kill 时 SIGTERM 整组
            )
        except FileNotFoundError as e:
            log_fp.close()
            raise ServiceError(f"spawn 失败 (命令找不到): {e}") from e

        rs = RunningService(
            app_name=app_name,
            service_name=spec.name,
            port=port,
            process=process,
            log_file_path=log_path,
            log_fp=log_fp,
            log_task=None,
            spec=spec,
        )

        # 7. 启动 log 转发 task
        rs.log_task = asyncio.create_task(_forward_streams(process, log_fp))

        # 8. TCP ready probe
        ready = await _wait_port_ready(port, timeout_ms=spec.startup_timeout_ms)
        if not ready:
            # spawn 起来但端口没 listen — kill + 报错
            await self._kill(rs)
            raise ServiceError(
                f"service {app_name}/{spec.name} 端口 {port} 在 {spec.startup_timeout_ms}ms 内没 ready "
                f"(看 {log_path} 查原因)"
            )

        logger.info(f"service_registry: ready {app_name}/{spec.name} port={port}")
        return rs

    async def _kill(self, rs: RunningService) -> None:
        """SIGTERM → 等 3s → SIGKILL. 同时 cancel log 转发 task + 关 log file."""
        proc = rs.process
        if proc is not None and proc.returncode is None:
            try:
                if proc.pid:
                    with contextlib.suppress(ProcessLookupError, PermissionError):
                        os.killpg(proc.pid, signal.SIGTERM)
            except Exception as e:
                logger.warning(f"service_registry: SIGTERM 失败 {rs.app_name}/{rs.service_name}: {e}")
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning(f"service_registry: {rs.app_name}/{rs.service_name} SIGTERM 后 3s 未退, SIGKILL")
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(proc.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await proc.wait()
        if rs.log_task is not None and not rs.log_task.done():
            rs.log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await rs.log_task
        with contextlib.suppress(Exception):
            rs.log_fp.write(
                f"=== service {rs.app_name}/{rs.service_name} stopped at "
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===\n"
            )
            rs.log_fp.close()
        logger.info(f"service_registry: killed {rs.app_name}/{rs.service_name}")


def service_registry() -> ServiceRegistry:
    return ServiceRegistry.instance()


# ─── helpers ─────────────────────────────────────────────────────

def _pick_free_port() -> int:
    """让系统分配空闲端口 — bind 0 拿到端口再 close, 之间有 race window
    (其他进程瞬间占走), 但 service spawn 速度极快, 实际碰撞概率极低. 不 ideal
    但跟同类 dev tools (vite / next dev) 一样的策略."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_port_ready(port: int, *, timeout_ms: int) -> bool:
    """循环 TCP connect 直到成功 / timeout. 50ms 一次, total max timeout_ms."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=0.5
            )
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.05)
    return False


async def _forward_streams(
    process: asyncio.subprocess.Process,
    log_fp,
) -> None:
    """把 stdout / stderr 实时写到 log file, 行首加 [stdout]/[stderr] 前缀.

    跟进程一起活. 进程退出 → stream EOF → 协程退出.
    """
    async def pump(stream, prefix: str) -> None:
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            try:
                log_fp.write(f"{prefix} {line.decode('utf-8', errors='replace')}")
                log_fp.flush()
            except Exception:
                return

    await asyncio.gather(
        pump(process.stdout, "[stdout]"),
        pump(process.stderr, "[stderr]"),
        return_exceptions=True,
    )
