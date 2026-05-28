"""browser-use CLI 的 in-process MCP 工具.

跟其它 in-process MCP server 不同, 这个 server 是 per-session 工厂构造:
LLM 在工具调用里拿不到 session id, 所以 PentaLoom.__init__ 时按 sid 算出
session_name + sandbox, 闭包注入三个工具函数. 其它 server 不关心 sid (操作绝对
路径或共享 uv 环境), 维持模块级 singleton.

三个工具:
  - install_browser_use(step): check / install / chromium 状态机
  - browser_use(command): 跑一条 CLI 子命令
  - browser_use_session_info(): 给"生成可复用脚本"流程拿 stable session 常量

HITL: install_browser_use 和 browser_use 走 can_use_tool, 由 tools/workspace.py
集中判定. session_info 只读, 不审.

提示词引导段在 prompts/tools.py.
"""

from __future__ import annotations

import platform
import shlex
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from pentaloom.capabilities import browser as browser_caps
from pentaloom.capabilities.browser._command import parse_browser_command
from pentaloom.config import Settings, get_settings
from pentaloom.infra import python_env

# ── 命名常量 (模块顶层, 跟 server 实例解耦) ─────────────────────────

BROWSER_MCP_SERVER_NAME = "pentaloom_browser"

INSTALL_TOOL_NAME = "install_browser_use"
BROWSER_USE_TOOL_NAME = "browser_use"
SESSION_INFO_TOOL_NAME = "browser_use_session_info"

INSTALL_BROWSER_USE_FULL_NAME = f"mcp__{BROWSER_MCP_SERVER_NAME}__{INSTALL_TOOL_NAME}"
BROWSER_USE_FULL_NAME = f"mcp__{BROWSER_MCP_SERVER_NAME}__{BROWSER_USE_TOOL_NAME}"
BROWSER_SESSION_INFO_FULL_NAME = (
    f"mcp__{BROWSER_MCP_SERVER_NAME}__{SESSION_INFO_TOOL_NAME}"
)

VALID_INSTALL_STEPS = frozenset({"check", "install", "chromium"})


# ── helpers ─────────────────────────────────────────────────────


def _browser_use_bin(python_env_dir: Path) -> Path:
    """共享 uv project venv 里 browser-use binary 的预期路径."""
    if platform.system() == "Windows":
        return python_env_dir / ".venv" / "Scripts" / "browser-use.exe"
    return python_env_dir / ".venv" / "bin" / "browser-use"


def _has_browser_use_cli(settings: Settings) -> bool:
    return _browser_use_bin(settings.python_env_dir).exists()


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _result_to_text(model: Any) -> str:
    return model.model_dump_json()


async def _run_browser_cli(
    settings: Settings,
    cli_args: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
) -> python_env.ScriptResult:
    """uv run --project <env> browser-use <args>."""
    return await python_env.run_uv_cli(
        settings,
        "browser-use",
        cli_args,
        cwd=cwd,
        timeout=timeout,
        timeout_message="browser-use command timed out",
    )


async def _run_browser_side_command(
    settings: Settings,
    command: str,
    *,
    session_args: list[str],
    cwd: Path,
) -> python_env.ScriptResult:
    """cookies 导入导出这类副车命令, 60s 超时."""
    args = list(session_args) + shlex.split(command)
    return await _run_browser_cli(settings, args, cwd=cwd, timeout=60)


async def _maybe_import_cookies(
    settings: Settings,
    sandbox: Path,
    *,
    action: str | None,
    session_args: list[str],
) -> None:
    if action != "open":
        return
    cookies_path = browser_caps.session_cookies_path(sandbox)
    if not cookies_path.exists():
        return
    await _run_browser_side_command(
        settings,
        f"cookies import {shlex.quote(str(cookies_path))}",
        session_args=session_args,
        cwd=sandbox,
    )


async def _maybe_export_cookies(
    settings: Settings,
    sandbox: Path,
    *,
    action: str | None,
    session_args: list[str],
) -> None:
    if action != "close":
        return
    cookies_path = browser_caps.session_cookies_path(sandbox)
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_browser_side_command(
        settings,
        f"cookies export {shlex.quote(str(cookies_path))}",
        session_args=session_args,
        cwd=sandbox,
    )


# ── install_browser_use 三步状态机 ──────────────────────────────


async def _do_install_check(settings: Settings) -> browser_caps.InstallStepResult:
    has_cli = _has_browser_use_cli(settings)
    has_browser = browser_caps.is_browser_available()
    if has_cli and has_browser:
        return browser_caps.InstallStepResult(
            step="check",
            success=True,
            message="browser-use CLI 和浏览器都已就绪, 可以直接调 browser_use.",
            next_step=None,
        )
    if not has_cli:
        return browser_caps.InstallStepResult(
            step="check",
            success=True,
            message=(
                "browser-use CLI 未安装. 下一步: 调 install_browser_use(step='install') 装包."
            ),
            next_step="install",
        )
    return browser_caps.InstallStepResult(
        step="check",
        success=True,
        message=(
            "系统未检测到 Chrome / Chromium. 下一步: 调 install_browser_use(step='chromium') "
            "装 Playwright Chromium."
        ),
        next_step="chromium",
    )


async def _do_install_install(
    settings: Settings, _index_url: str | None
) -> browser_caps.InstallStepResult:
    if _has_browser_use_cli(settings):
        return browser_caps.InstallStepResult(
            step="install",
            success=True,
            message="browser-use 已经装在共享 uv project 里, 跳过.",
            next_step=None if browser_caps.is_browser_available() else "chromium",
        )

    # index_url 当前没用上 — uv add 用 --index 不是 --index-url, 后续如果用户有需求
    # 再补. 同时用户可以通过 UV_INDEX_URL 环境变量调.
    result = await python_env.install_libs(settings, ["browser-use"], timeout=600)
    if not result.success:
        return browser_caps.InstallStepResult(
            step="install",
            success=False,
            message=(
                f"uv add browser-use 失败 (exit={result.exit_code}). "
                f"stderr: {result.stderr[-1000:]}"
            ),
            next_step=None,
        )
    return browser_caps.InstallStepResult(
        step="install",
        success=True,
        message="browser-use 已装到共享 uv project.",
        next_step=None if browser_caps.is_browser_available() else "chromium",
    )


async def _do_install_chromium(settings: Settings) -> browser_caps.InstallStepResult:
    if browser_caps.is_browser_available():
        msg = (
            "检测到系统 Chrome, 跳过 Chromium 安装."
            if browser_caps.is_system_chrome_installed()
            else "Chromium 已存在 (Playwright 缓存)."
        )
        return browser_caps.InstallStepResult(
            step="chromium", success=True, message=msg, next_step=None
        )

    env = python_env.build_env(settings)
    cmd = [
        python_env.uv_bin(env),
        "tool",
        "run",
        "playwright",
        "install",
        "chromium",
    ]
    result = await python_env.run_command(
        cmd,
        cwd=settings.python_env_dir,
        env=env,
        timeout=900,
        timeout_message="playwright install chromium timed out",
    )
    if result.success or browser_caps.is_chromium_installed():
        return browser_caps.InstallStepResult(
            step="chromium",
            success=True,
            message="Chromium 已装好.",
            next_step=None,
        )
    return browser_caps.InstallStepResult(
        step="chromium",
        success=False,
        message=(
            f"playwright install chromium 失败 (exit={result.exit_code}). "
            f"stderr: {result.stderr[-1000:]}"
        ),
        next_step=None,
    )


# ── server factory ───────────────────────────────────────────────


def build_browser_mcp_server(session_name: str, sandbox: Path):
    """构造 per-session MCP server. session_name 跟 sandbox 被三个工具闭包持有."""

    @tool(
        INSTALL_TOOL_NAME,
        (
            "分步安装 browser-use 环境. 用户授权后执行. "
            "step='check' 检查当前状态 (CLI 装没装 / 系统有没有 Chrome), 返回 next_step 引导下一步. "
            "step='install' 把 browser-use 装到共享 uv project (内跑 uv add). "
            "step='chromium' 装 Playwright Chromium (仅在系统无 Chrome 时需要). "
            "参数: step ('check' | 'install' | 'chromium'), index_url (可选 PyPI 镜像, 当前忽略)."
        ),
        {"step": str, "index_url": str},
    )
    async def _install_browser_use(args: dict[str, Any]) -> dict[str, Any]:
        step = str(args.get("step", "check")).strip()
        index_url = args.get("index_url")
        if step not in VALID_INSTALL_STEPS:
            return _err(
                f"step 必须是 check / install / chromium 之一, 收到 {step!r}"
            )

        settings = get_settings()
        try:
            if step == "check":
                result = await _do_install_check(settings)
            elif step == "install":
                idx = index_url if isinstance(index_url, str) and index_url else None
                result = await _do_install_install(settings, idx)
            else:
                result = await _do_install_chromium(settings)
        except Exception as e:  # noqa: BLE001 — 工具边界吞异常, 错误信息给 LLM
            return _err(f"install_browser_use step={step} 异常: {e}")

        return {
            "content": [{"type": "text", "text": _result_to_text(result)}],
            "is_error": not result.success,
        }

    @tool(
        BROWSER_USE_TOOL_NAME,
        (
            "跑一条 browser-use CLI 子命令. 用户授权后执行 (按 action verb 攒会话级免审). "
            "命令体跟 browser-use CLI 一致: open URL / state / click N / type N text / "
            "eval ... / screenshot [path] / switch N / close 等. "
            "--session 不要手传 — 工具自动注入 per-session 名. "
            "--profile / --cdp-url / --connect / --browser / --headed 这些 global flag "
            "在 open 时落盘, 后续动作自动重放, 不需要重复传. "
            "open 前自动导入会话 cookies, close 前自动导出. "
            "参数: command (一整条 CLI 字符串)."
        ),
        {"command": str},
    )
    async def _browser_use(args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command", "")).strip()
        if not command:
            return _err("command 不能为空.")

        settings = get_settings()
        if not _has_browser_use_cli(settings):
            return _err(
                "browser-use CLI 未安装. 请先调 install_browser_use(step='check') 看下一步."
            )
        if not browser_caps.is_browser_available():
            return _err(
                "未检测到浏览器. 请先调 install_browser_use(step='chromium') 装 Chromium "
                "(或自行装系统 Chrome)."
            )

        sandbox.mkdir(parents=True, exist_ok=True)
        processed = browser_caps.prepare_browser_command(command, sandbox)
        session_args, config_to_save = browser_caps.build_session_args(
            processed, sandbox=sandbox, session_name=session_name
        )
        _, action = parse_browser_command(processed)

        # close 前导出 cookies, open 前导入. 失败不阻塞主命令.
        try:
            await _maybe_export_cookies(
                settings, sandbox, action=action, session_args=session_args
            )
            await _maybe_import_cookies(
                settings, sandbox, action=action, session_args=session_args
            )
        except Exception:  # noqa: BLE001 — cookie 副车失败不影响主命令
            pass

        cli_args = list(session_args) + shlex.split(processed)
        # open 可能要等用户在浏览器里手动操作, 给宽点的 timeout (5 分钟)
        timeout = 300 if action == "open" else 120
        result = await _run_browser_cli(settings, cli_args, cwd=sandbox, timeout=timeout)
        output = (result.stdout or "") + (result.stderr or "")

        # 命令成功 (exit 0) 才把 effective config 落盘. 失败的 open 不能存 — 否则
        # 像 --profile 不兼容这种 explicit 标志会永久卡在 session.json, 后续每条
        # 命令自动重放, 一辈子走不出去.
        if result.exit_code == 0 and config_to_save is not None:
            try:
                browser_caps.save_session_config(sandbox, config_to_save)
            except OSError:
                pass

        # 用 raw command (LLM 写的) 判断, 不用 processed (内部规范化过)
        if browser_caps.is_state_command(command):
            output = (
                "🚫 STOP: `state` 输出只用于定位元素, 可能被截断或不完整.\n\n"
                "**禁止**把 state 文本直接当作最终回答.\n"
                "- 单个具体元素: 用 `get text <index>` 拿完整值\n"
                "- 列表 / 重复数据: 用 `eval` 显式抽取, 不要从 state 直接概括\n"
                "- 优先 scoped 抽取, 少用 page-wide document.body.innerText\n\n"
                + output
            )
        if browser_caps.is_switch_command(command):
            output = (
                "🚫 STOP: `switch <index>` 本身不算完成 tab 检查.\n\n"
                "如果当前 tab 是空白 / 无关 / 非预期, 必须**继续 switch 剩余 tab**, "
                "再决定是否回到原 tab.\n"
                "不要看到一个错 tab 就当作'没有新页', 也不要立刻切回去.\n\n"
                + output
            )

        payload = browser_caps.BrowserUseResult(command=command, output=output)
        return {
            "content": [{"type": "text", "text": _result_to_text(payload)}],
            "is_error": result.exit_code != 0,
        }

    @tool(
        SESSION_INFO_TOOL_NAME,
        (
            "返回当前 session 的 browser-use 常量 (session_name / profile / cookies_path 等). "
            "用于生成可复用 Python 脚本时把这些值写死成常量. "
            "只读, 不需要授权. 无参数."
        ),
        {},
    )
    async def _browser_use_session_info(_args: dict[str, Any]) -> dict[str, Any]:
        stored = browser_caps.load_session_config(sandbox)
        cookies_path = browser_caps.session_cookies_path(sandbox)
        info = browser_caps.BrowserSessionInfoResult(
            session_name=session_name,
            profile=stored.profile,
            headed=stored.headed,
            cdp_url=stored.cdp_url,
            connect=stored.connect,
            browser=stored.browser,
            cookies_path=str(cookies_path),
        )
        return _ok(_result_to_text(info))

    return create_sdk_mcp_server(
        name=BROWSER_MCP_SERVER_NAME,
        tools=[_install_browser_use, _browser_use, _browser_use_session_info],
    )
