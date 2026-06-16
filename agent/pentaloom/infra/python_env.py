"""Python 脚本执行 + 依赖装载基建.

设计 (见 docs/file-capability.md §4.3):
  - 共享 uv project 在 settings.python_env_dir, 所有 session 用同一个 venv. 起步够用,
    后面冲突再切 per-session.
  - uv add 由用户授权 (install_python_libs 工具走 HITL); uv run 由用户授权
    (run_python_script 工具走 HITL, 默认 allow_once — 脚本内容每次都不一样).
  - 脚本本体存 session 沙箱里, run_python_script 接 script_path 而不是 inline body,
    避开 LLM 把代码塞进参数导致 escaping 失控.
  - timeout 触发后 kill 整个 process group, 防 matplotlib 之类 fork 出来的子进程残留.

环境构建策略:
  - 用 login shell 抓一次 PATH (Electron 从 Finder 拉起来的 main process 不跑 .zshrc,
    bare PATH 找不到 uv). 抓完缓存, 后续子进程都用这份.
  - uv cache / Python 安装目录都重定向到 settings.data_dir 下, 卸载 PentaLoom 时
    一键清光, 不污染 ~/.cache.
  - MPLCONFIGDIR 也指到 data_dir, 防 macOS 沙箱权限挡 matplotlib 默认路径.
  - uv 绝对路径显式 resolve (Windows CreateProcessW 不查 PATH).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import platform
import py_compile
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from pentaloom.config import Settings

# 预装一批高频包. 用户没明确装的话, 大部分文件场景靠这几个就能起步:
#   reportlab / pypdf : PDF 读写
#   python-pptx       : PowerPoint
#   python-docx       : Word
#   pandas / matplotlib: 数据 / 出图
# 装失败不阻塞启动 — 用户后面再让 install_python_libs 装即可.
PREINSTALL_PACKAGES: tuple[str, ...] = (
    "reportlab",
    "python-pptx",
    "python-docx",
    "pandas",
    "matplotlib",
    "pypdf",
)

# uv add 装一批包可能要几分钟 (尤其首次, 要下 wheel + 编译).
_PREWARM_TIMEOUT = 600
_INSTALL_TIMEOUT = 300
_RUN_TIMEOUT = 60

_PYPROJECT_TEMPLATE = """\
[project]
name = "pentaloom-python-env"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = []
"""


@dataclass
class ScriptResult:
    """uv add / uv run 的统一回报结构. 工具 @tool 函数序列化成 dict 给 SDK."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "success": self.success,
        }


# ── Login shell PATH 抓取 (一次性, 缓存) ─────────────────────────

_login_env: dict[str, str] | None = None


def _capture_login_env() -> dict[str, str]:
    """跑一次 login shell + source rc 文件, 拿到带完整 PATH 的 env.

    Electron 桌面 app 从 GUI 启动时, main process 的 PATH 极简 (/usr/bin:/bin 之类),
    用户在 .zshrc 里加的 brew / cargo / pyenv 路径完全看不到, 直接调 uv 会 ENOENT.

    Windows 没这问题, os.environ 已经完整.
    """
    if platform.system() == "Windows":
        return dict(os.environ)

    shell = os.environ.get("SHELL", "/bin/zsh")
    shell_name = Path(shell).name
    # zsh -l / bash -l 只跑 .zprofile/.profile, 不跑交互式 rc, 用户的 PATH 一般在
    # .zshrc 里, 必须显式 source.
    rc_source = {
        "zsh": "source ~/.zshrc 2>/dev/null; ",
        "bash": "source ~/.bashrc 2>/dev/null; ",
    }.get(shell_name, "")
    seed = {
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
        "SHELL": shell,
        "TERM": "xterm",
    }
    try:
        r = subprocess.run(
            [shell, "-l", "-c", f"{rc_source}env -0"],
            capture_output=True,
            text=True,
            timeout=10,
            env=seed,
        )
        return {
            k: v
            for e in r.stdout.split("\0")
            if "=" in e
            for k, _, v in [e.partition("=")]
        }
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"login shell PATH 抓取失败, 退回 os.environ: {e}")
        return dict(os.environ)


def build_env(settings: Settings) -> dict[str, str]:
    """给 uv / python 子进程的环境变量.

    含三件事:
      1. 从 login shell 抓完整 PATH (uv 才找得到)
      2. UV_CACHE_DIR / UV_PYTHON_INSTALL_DIR 收编到 data_dir, 不污染 ~/.cache
      3. MPLCONFIGDIR 指到 data_dir, 防 macOS 沙箱挡 matplotlib 默认路径
    """
    global _login_env
    if _login_env is None:
        _login_env = _capture_login_env()
    env = _login_env.copy()

    # uv 自管目录都收到 data_dir 下, 卸载 app 一键清.
    env.setdefault("UV_CACHE_DIR", str(settings.data_dir / ".uv-cache"))
    env.setdefault(
        "UV_PYTHON_INSTALL_DIR", str(settings.data_dir / ".uv-python")
    )
    # 锁 3.12, 防 uv 挑别的版本导致 wheel resolution 不一致.
    env.setdefault("UV_PYTHON", "3.12")

    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    mpl_dir = settings.data_dir / ".matplotlib"
    mpl_dir.mkdir(exist_ok=True)
    env["MPLCONFIGDIR"] = str(mpl_dir)

    return env


def uv_bin(env: dict[str, str]) -> str:
    """显式 resolve uv 绝对路径.

    Windows CreateProcessW 不查 PATH, asyncio.create_subprocess_exec("uv", ...)
    会 FileNotFoundError 即使 env['PATH'] 里有. Unix 上也保险一点.
    """
    resolved = shutil.which("uv", path=env.get("PATH", ""))
    return resolved or "uv"


# ── 子进程清理 ─────────────────────────────────────────────────

def _kill_process_group(pid: int) -> None:
    """timeout / cancel 时清掉整个进程树, 防 matplotlib 等 fork 出来的子进程残留."""
    if platform.system() == "Windows":
        with contextlib.suppress(OSError):
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True
            )
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGTERM)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


async def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    timeout_message: str,
    stdin_data: bytes | None = None,
) -> ScriptResult:
    """跑子进程, 超时 kill process group, 永远返回 ScriptResult (不抛).

    stdin_data: 非 None 时打开 PIPE 把它喂进去 (给 invocable app 走 stdin JSON 协议).
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data), timeout=timeout,
        )
    except asyncio.TimeoutError:
        _kill_process_group(proc.pid)
        await proc.wait()
        return ScriptResult(
            exit_code=-1,
            stdout="",
            stderr=f"{timeout_message} after {timeout} seconds",
        )
    except asyncio.CancelledError:
        _kill_process_group(proc.pid)
        await proc.wait()
        raise

    return ScriptResult(
        exit_code=proc.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


# 公开别名: 模块外想跑任意命令时用. 内部 install_libs / run_script / run_uv_cli
# 都走同一份实现, 不重复造 timeout / process group 处理.
run_command = _run


# ── uv project 管理 ────────────────────────────────────────────

def ensure_uv_project(python_env_dir: Path) -> None:
    """没 pyproject.toml 就建一个空的. .venv 不建 — 留给 uv sync / uv add 懒建."""
    python_env_dir.mkdir(parents=True, exist_ok=True)
    pyproject = python_env_dir / "pyproject.toml"
    if not pyproject.exists():
        pyproject.write_text(_PYPROJECT_TEMPLATE, encoding="utf-8")


async def prewarm(settings: Settings) -> None:
    """lifespan 异步调. 失败只 warn 不抛 — 用户后面 install_python_libs 也能补.

    顺序: ensure project → uv sync (建 .venv) → uv add 预装包.
    sync 已隐含拉 .venv, 但 dependencies 是空的所以基本秒回. uv add 才是真活儿.
    """
    python_env_dir = settings.python_env_dir
    ensure_uv_project(python_env_dir)
    env = build_env(settings)
    uv = uv_bin(env)

    sync_result = await _run(
        [uv, "sync"],
        cwd=python_env_dir,
        env=env,
        timeout=_PREWARM_TIMEOUT,
        timeout_message="uv sync timed out",
    )
    if not sync_result.success:
        logger.warning(
            f"prewarm uv sync 失败 (exit={sync_result.exit_code}), 略过预装. "
            f"stderr={sync_result.stderr[:500]}"
        )
        return

    add_result = await _run(
        [uv, "add", *PREINSTALL_PACKAGES],
        cwd=python_env_dir,
        env=env,
        timeout=_PREWARM_TIMEOUT,
        timeout_message="uv add prewarm timed out",
    )
    if add_result.success:
        logger.info(
            f"prewarm 完成, 预装 {len(PREINSTALL_PACKAGES)} 个包 → {python_env_dir}"
        )
    else:
        logger.warning(
            f"prewarm uv add 失败 (exit={add_result.exit_code}). "
            f"stderr={add_result.stderr[:500]}"
        )


async def install_libs(
    settings: Settings, libs: list[str], *, timeout: int = _INSTALL_TIMEOUT
) -> ScriptResult:
    """uv add <libs>. 由 install_python_libs 工具调."""
    if not libs:
        return ScriptResult(exit_code=0, stdout="", stderr="no libs to install")
    ensure_uv_project(settings.python_env_dir)
    env = build_env(settings)
    return await _run(
        [uv_bin(env), "add", *libs],
        cwd=settings.python_env_dir,
        env=env,
        timeout=timeout,
        timeout_message="uv add timed out",
    )


async def run_uv_cli(
    settings: Settings,
    bin_name: str,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = _RUN_TIMEOUT,
    timeout_message: str | None = None,
) -> ScriptResult:
    """跑共享 uv project 里装的 CLI: uv run --project <env> <bin_name> [args...].

    给非 python 入口的 CLI 用 (比如 browser-use, playwright). cwd 不传时落到 sandbox 外
    的 python_env_dir, 跟 install/run_script 一致. 用户工具通常应该传 sandbox 让相对路径
    落到对应会话目录里.
    """
    ensure_uv_project(settings.python_env_dir)
    env = build_env(settings)
    cmd = [
        uv_bin(env),
        "run",
        "--project",
        str(settings.python_env_dir),
        bin_name,
        *args,
    ]
    return await _run(
        cmd,
        cwd=cwd or settings.python_env_dir,
        env=env,
        timeout=timeout,
        timeout_message=timeout_message or f"uv run {bin_name} timed out",
    )


# ── 脚本执行 ───────────────────────────────────────────────────

def _preflight_compile(script_path: Path) -> str | None:
    """py_compile 预检, 让语法错误在拉 uv 前就暴露 (省 ~1s subprocess 启动 + 错误更准).

    返回错误描述或 None.
    """
    try:
        py_compile.compile(str(script_path), doraise=True)
        return None
    except py_compile.PyCompileError as e:
        return str(e)
    except FileNotFoundError:
        return f"script not found: {script_path}"


async def run_script(
    settings: Settings,
    script_path: Path,
    *,
    args: list[str] | None = None,
    timeout: int = _RUN_TIMEOUT,
    preflight_compile: bool = True,
) -> ScriptResult:
    """uv run --project <python_env_dir> python <script_path> [args...].

    cwd 故意设成脚本所在目录 — 这样脚本里 open("output.pdf") 这样的相对路径
    就落在 session 沙箱里, 不会污染 python_env_dir.
    """
    if preflight_compile:
        err = _preflight_compile(script_path)
        if err is not None:
            return ScriptResult(exit_code=2, stdout="", stderr=err)

    env = build_env(settings)
    cmd = [
        uv_bin(env),
        "run",
        "--project",
        str(settings.python_env_dir),
        "python",
        str(script_path),
        *(args or []),
    ]
    return await _run(
        cmd,
        cwd=script_path.parent,
        env=env,
        timeout=timeout,
        timeout_message="script execution timed out",
    )


async def run_app_script(
    settings: Settings,
    *,
    cwd: Path,
    command: list[str],
    stdin_data: bytes,
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> ScriptResult:
    """Invocable App 的 script invocation runtime.

    跟 run_script 不同: command 是 app.json 里 ScriptComponent.command 整段 (e.g.,
    ['python', 'scripts/render.py']), cwd 是 app 的 files/ 目录, stdin 喂 JSON args,
    stdout 出 JSON result. 不做 preflight_compile (script 可能不是 .py 文件; 真
    syntax 错的话 spawn 自然 fail).

    走 uv run --project 共享 python_env_dir, 复用 install_python_libs 装好的依赖.

    extra_env: 注入 PENTALOOM_LOOM / FILES_DIR / RUNTIME_DIR 等 weaver
    上下文 (见 capabilities/weaver/app_env.py); script 拿这些反向调能力.
    """
    env = build_env(settings)
    if extra_env:
        env.update(extra_env)
    # 第一个 token 是 "python" / "node" / "bash" 等. python 类的走 uv 隔离, 其他
    # 直接 exec (PATH 走 env). 当前只验 python; 其他类型后续扩展.
    if command and command[0] == "python":
        uv_cmd = [
            uv_bin(env), "run", "--project", str(settings.python_env_dir),
        ] + command
    else:
        uv_cmd = list(command)
    return await _run(
        uv_cmd,
        cwd=cwd,
        env=env,
        timeout=timeout,
        timeout_message="app invocation timed out",
        stdin_data=stdin_data,
    )
