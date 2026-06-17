"""App 级 Python 依赖隔离 — 每个 app 一个独立 uv workspace + .venv.

设计目标:
  - PentaLoom 平台依赖 (settings.python_env_dir 共享 venv) 跟 app 依赖严格分开
  - 每个 invocable app 在 weaver/apps/<name>/files/ 下有自己的 pyproject.toml + .venv
  - service / script / schedule 跑 Python 命令时走 `uv run python ...` 在 app workspace
  - 用户在 component spec 上声明 python_deps, finalize 时收集去重 + uv add 装到 app workspace
  - SQLite (sqlite3) 是 stdlib, 不该出现在 python_deps 里, 用户不写就不装

不做:
  - per-component venv (每个 service/script 独立 venv, 太重)
  - global fallback (app 找不到的库不去共享 venv 兜底, 强制声明)
  - 复杂版本仲裁 (uv 自己管, 依赖冲突用户写到 pyproject 解决)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from pentaloom.config import Settings
from pentaloom.infra import python_env

if TYPE_CHECKING:
    from pentaloom.capabilities.weaver.models import AppDefinition


_APP_PYPROJECT_TEMPLATE = '''[project]
name = "pentaloom-app-{safe_name}"
version = "0.1.0"
description = "PentaLoom invocable app workspace (managed by weaver)"
requires-python = ">=3.11"
dependencies = []
'''


# pyproject.toml [project].name 必须 PEP 503 normalized. app name 已经 kebab-case
# (a-z0-9-), 但保险起见再过滤一遍 — 防新规则放宽时这里出错.
_PYPROJECT_NAME_RE = re.compile(r"[^a-z0-9-]")


def _safe_pyproject_name(app_name: str) -> str:
    """把 app name 规整成合法 PEP 503 name. kebab-case 输入直接过."""
    safe = _PYPROJECT_NAME_RE.sub("-", app_name.lower())
    safe = safe.strip("-") or "app"
    return safe[:60]  # 防过长


def ensure_app_uv_project(files_root: Path, app_name: str) -> Path:
    """确保 app 的 files/ 是一个 uv project (有 pyproject.toml).

    files_root 不存在会被建; pyproject.toml 不存在会写一份最小模板. .venv 不主动建,
    留给后续 uv add / uv sync 触发.

    返 pyproject.toml 绝对路径.
    """
    files_root.mkdir(parents=True, exist_ok=True)
    pyproject = files_root / "pyproject.toml"
    if pyproject.exists():
        return pyproject
    body = _APP_PYPROJECT_TEMPLATE.format(safe_name=_safe_pyproject_name(app_name))
    pyproject.write_text(body, encoding="utf-8")
    logger.info(f"app workspace pyproject created: {pyproject}")
    return pyproject


def collect_python_deps(app_def: "AppDefinition") -> list[str]:
    """从 services / scripts / schedules 收集 python_deps, 去重保持插入顺序, 滤掉空串.

    不去自动推断 (e.g., scan source files for `import fastapi`) — 静态分析不可靠,
    用户该声明就声明. stdlib 不在收集范围, 用户不该写进 python_deps (写了 uv add 会
    报包不存在; 用户自己看错). 见 P3 §1 校验.
    """
    seen: set[str] = set()
    out: list[str] = []
    pools = [
        app_def.components.services,
        app_def.components.scripts,
        app_def.components.schedules,
    ]
    for comps in pools:
        for c in comps:
            deps = getattr(c, "python_deps", None) or []
            for d in deps:
                d = str(d).strip()
                if not d or d in seen:
                    continue
                seen.add(d)
                out.append(d)
    return out


async def install_app_python_deps(
    settings: Settings,
    files_root: Path,
    app_name: str,
    deps: list[str],
    *,
    timeout: int = 120,
) -> None:
    """在 app workspace 下 uv add <deps>. 装 app 自己的 .venv, 不动平台 venv.

    deps 空时只 ensure pyproject (建 workspace 但不调 uv add — uv 装空列表会失败).
    deps 非空时:
      - ensure pyproject
      - cwd=files_root, uv add <deps>
      - 失败: 抛 RuntimeError, 文案带 deps 列表 + stderr 末段 (调用方接住转 finalize 失败)

    timeout 默认 120s. 首次装 fastapi/uvicorn 这种含 wheel + 编译的可能慢, 但比平台
    venv 已经有的情况下 uv add 会基本秒回.
    """
    ensure_app_uv_project(files_root, app_name)
    if not deps:
        logger.info(f"app {app_name}: 无 python_deps, 跳过 uv add (workspace 已就绪)")
        return

    env = python_env.build_env(settings)
    uv = python_env.uv_bin(env)
    logger.info(f"app {app_name}: uv add {deps} → {files_root}")
    result = await python_env.run_command(
        [uv, "add", *deps],
        cwd=files_root,
        env=env,
        timeout=timeout,
        timeout_message=f"uv add app deps timed out ({timeout}s)",
    )
    if not result.success:
        # stderr 优先, 没 stderr 用 stdout (uv 偶尔把错塞 stdout)
        err_tail = (result.stderr or result.stdout or "")[-500:].strip()
        raise RuntimeError(
            f"app {app_name!r} python_deps 装失败 (uv add exit={result.exit_code}): "
            f"{deps}. stderr 末段:\n{err_tail}"
        )
    logger.info(f"app {app_name}: deps 装好 ({len(deps)} 个)")


def python_command_for_app(
    settings: Settings, command: list[str], files_root: Path,
) -> list[str]:
    """把 app spec 的 Python command 转成 app workspace `uv run` 命令.

    输入 → 输出:
      ["python", "x.py"]                  → ["uv", "run", "--project", <files>, "python", "x.py"]
      ["python", "-u", "services/api.py"] → ["uv", "run", "--project", <files>, "python", "-u", "services/api.py"]
      ["python3", "scripts/x.py"]         → ["uv", "run", "--project", <files>, "python3", "scripts/x.py"]
      ["uv", "run", "python", "x.py"]     → ["uv", "run", "--project", <files>, "python", "x.py"]
                                            (剥外层 uv run 重新包, 保证带 --project app)
      ["node", "server.js"]               → ["node", "server.js"]   (非 Python, 原样返)
      ["./bin/foo"]                       → ["./bin/foo"]            (非 Python)

    要让 cwd 落在 files_root 或其子目录 (workdir), 调用方负责.
    """
    if not command:
        return list(command)
    cmd = list(command)

    # 先剥可能的外层 uv run 前缀 (避免 uv run uv run 双层包 / 错指 platform venv)
    if len(cmd) >= 2 and cmd[0] == "uv" and cmd[1] == "run":
        cmd = cmd[2:]
        # 跳一个可能的 --project <path>
        if len(cmd) >= 2 and cmd[0] == "--project":
            cmd = cmd[2:]

    if not cmd or cmd[0] not in {"python", "python3"}:
        return list(command)  # 非 Python, 原样

    env = python_env.build_env(settings)
    uv = python_env.uv_bin(env)
    return [uv, "run", "--project", str(files_root), *cmd]
