"""weaver_runner — launchd 调起的 wrapper, 跑 service / schedule / watch 三类组件.

每个 service / schedule / watch 各有一份独立 launchd plist, ProgramArguments 是:
    <venv>/python -m pentaloom.weaver_runner --app=X --component=Y --kind=Z

Z ∈ {service, schedule, watch}.

关键点:
- service: 这条命令 **execvp 替换成用户的 service 进程**, launchd 监督真 service.
  端口分配 + python_deps 装 + env 注入都在 execvp 之前做完.
- schedule / watch: 一次性跑用户的 invocation (target=script), 调 invoke_app 路径,
  写 runs.jsonl, 跑完退出. launchd 不期望 schedule/watch 常驻.

跟 PentaLoom server 完全解耦 — server 不在跑也能 launch (launchd 直接调起 wrapper).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(prog="pentaloom.weaver_runner")
    parser.add_argument("--app", required=True, help="app name (matches weaver/apps/<name>)")
    parser.add_argument("--component", required=True, help="service/schedule/watch component name")
    parser.add_argument("--kind", required=True, choices=["service", "schedule", "watch"])
    args = parser.parse_args()

    if args.kind == "service":
        # service: 同步 + execvp 替换进程, 不能 asyncio.run (因为我们要 exec 走人)
        run_service(args.app, args.component)
    elif args.kind == "schedule":
        asyncio.run(run_schedule(args.app, args.component))
    elif args.kind == "watch":
        asyncio.run(run_watch(args.app, args.component))


# ────────────────────────────────────────────────────────────────────
# service: execvp 替换进程
# ────────────────────────────────────────────────────────────────────


def run_service(app_name: str, comp_name: str) -> None:
    """读 spec → 装 deps → 准备 env/cwd → execvp 替换进程.

    execvp 之后这个 Python wrapper 已经被替换掉, launchd 监督的是真 service 进程.
    """
    from pentaloom.capabilities.weaver import app as app_biz
    from pentaloom.capabilities.weaver import app_python_env
    from pentaloom.capabilities.weaver import paths
    from pentaloom.config import get_settings
    from pentaloom.infra import python_env

    settings = get_settings()
    app_def = app_biz.read_app_definition(settings, app_name)
    if app_def is None:
        _die(f"app.json missing for {app_name!r}")

    spec = next(
        (s for s in app_def.components.services if s.name == comp_name), None
    )
    if spec is None:
        _die(f"service {comp_name!r} not in {app_name}/app.json")

    files_root = paths.app_files_dir(settings, app_name)

    # python_deps 装进 app workspace (不进平台共享 venv). finalize 时已装过一次,
    # 这里 idempotent — 已装的 uv add 秒回. 防 declared 复用 launchd 起来时 venv
    # 被外部清掉 / 跨机器搬动. 注意: declared 走全局 launchd 调起来, 不能 raise,
    # 用 _die 退出让 launchd 看 exit!=0.
    try:
        asyncio.run(app_python_env.install_app_python_deps(
            settings, files_root, app_name, list(spec.python_deps or []),
        ))
    except RuntimeError as e:
        _die(str(e))

    # 端口 — spec.port 给了用它, 没给找系统空闲. 写文件给 _invoke_service 读.
    port = spec.port if spec.port else _pick_free_port()
    _write_port_file(settings, app_name, comp_name, port)

    # cwd — workdir 必须在 files/ 内 (跟 service_registry 同款防穿越)
    if spec.workdir:
        cwd = (files_root / spec.workdir).resolve()
        try:
            cwd.relative_to(files_root.resolve())
        except ValueError:
            _die(f"workdir {spec.workdir!r} 越出 files/ 根")
        if not cwd.is_dir():
            _die(f"workdir {spec.workdir!r} 不是目录: {cwd}")
    else:
        cwd = files_root

    # env — 跟 _invoke_script 同款 weaver 上下文 + service 专属字段 (port/name)
    from pentaloom.capabilities.weaver.app_env import weaver_app_env
    env = python_env.build_env(settings)
    env.update(weaver_app_env(
        settings, app_name,
        service_name=spec.name, service_port=port,
    ))

    # command — Python 命令 (含 ["uv","run","python",...] 前缀) 一律重写成
    # `uv run --project <files_root> python ...`, service 用 app .venv 跑.
    command = app_python_env.python_command_for_app(
        settings, list(spec.command), files_root,
    )

    # 6. execvp — 替换进程, launchd 监督真 service. 不返.
    sys.stderr.write(
        f"[weaver_runner] service {app_name}/{spec.name} port={port} cwd={cwd}\n"
    )
    sys.stderr.flush()
    os.chdir(cwd)
    os.execvpe(command[0], command, env)


# ────────────────────────────────────────────────────────────────────
# schedule / watch: 跑 invoke_app
# ────────────────────────────────────────────────────────────────────


async def run_schedule(app_name: str, comp_name: str) -> None:
    """schedule 触发: 一次性跑对应 invocation, 写 runs.jsonl 然后退出."""
    from pentaloom.capabilities.weaver import app as app_biz
    from pentaloom.capabilities.weaver.app_runtime import InvokeError, invoke_app
    from pentaloom.config import get_settings

    settings = get_settings()
    app_def = app_biz.read_app_definition(settings, app_name)
    if app_def is None:
        _die(f"app.json missing for {app_name!r}")
    spec = next(
        (s for s in app_def.components.schedules if s.name == comp_name), None
    )
    if spec is None:
        _die(f"schedule {comp_name!r} not in {app_name}/app.json")

    try:
        result = await invoke_app(
            settings,
            app_name=app_name,
            invocation_id=spec.invocation_id,
            args=dict(spec.args),
            trigger="schedule",
        )
        print(
            f"[schedule] {app_name}/{comp_name}: {result['status']} "
            f"run={result['run_id']} {result.get('duration_ms', 0)}ms"
        )
    except InvokeError as e:
        _die(f"[schedule] {app_name}/{comp_name} FAILED: {e}")


async def run_watch(app_name: str, comp_name: str) -> None:
    """watch 触发: launchd WatchPaths 路径一变就调起 wrapper. 跑 invocation."""
    from pentaloom.capabilities.weaver import app as app_biz
    from pentaloom.capabilities.weaver.app_runtime import InvokeError, invoke_app
    from pentaloom.config import get_settings

    settings = get_settings()
    app_def = app_biz.read_app_definition(settings, app_name)
    if app_def is None:
        _die(f"app.json missing for {app_name!r}")
    spec = next(
        (w for w in app_def.components.watches if w.name == comp_name), None
    )
    if spec is None:
        _die(f"watch {comp_name!r} not in {app_name}/app.json")
    if not spec.invocation_id:
        # 仅 UI 浏览模式, plist 不该被渲, 走到这是异常 — 静默退出.
        return

    # launchd WatchPaths 不给具体 event 类型, args.events 留空 list.
    args: dict[str, Any] = {**spec.args, "events": []}

    try:
        result = await invoke_app(
            settings,
            app_name=app_name,
            invocation_id=spec.invocation_id,
            args=args,
            trigger="watch",
        )
        print(
            f"[watch] {app_name}/{comp_name}: {result['status']} "
            f"run={result['run_id']} {result.get('duration_ms', 0)}ms"
        )
    except InvokeError as e:
        _die(f"[watch] {app_name}/{comp_name} FAILED: {e}")


# ────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────


def _pick_free_port() -> int:
    """让 OS 分配空闲端口."""
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _write_port_file(settings, app_name: str, comp_name: str, port: int) -> None:
    """端口落到 ~/.pentaloom/sandboxes/<app>/.runtime/<svc>.port — _invoke_service 读."""
    from pentaloom.capabilities.weaver import paths

    runtime_dir = paths.app_dir(settings, app_name) / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / f"{comp_name}.port").write_text(str(port))


def read_port_file(settings, app_name: str, comp_name: str) -> int | None:
    """给 _invoke_service 读端口用. 文件不存在 → None (service 还没起)."""
    from pentaloom.capabilities.weaver import paths

    p = paths.app_dir(settings, app_name) / ".runtime" / f"{comp_name}.port"
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except (ValueError, OSError):
        return None


def _die(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()
    sys.exit(1)


if __name__ == "__main__":
    main()
