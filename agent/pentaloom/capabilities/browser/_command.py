"""browser-use 命令解析 + 规范化 + session 参数注入.

PentaLoom 走 in-process MCP, 工具拿不到 ctx, 调用方必须显式传 sandbox + session_name.

涉及的命令形态 (browser-use CLI 0.x):
    [global-flags...] <action> [action-args...]
  global-flags: --headed / --session NAME / --profile NAME / --cdp-url URL
                / --connect / --browser X / -p / -s / -b
  action: open / state / click / dblclick / rightclick / hover / type / input
          / keys / select / upload / eval / extract / wait / get / switch
          / back / scroll / screenshot / cookies / close-tab / close / ...
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from pentaloom.capabilities.browser._models import StoredSessionConfig
from pentaloom.capabilities.browser._paths import load_session_config

# state / click 等"会用到已存 session 配置"的动作. open 也属于但单独处理 (强制 headed).
_REPLAY_ACTIONS = frozenset({
    "state", "click", "dblclick", "rightclick", "hover",
    "type", "input", "keys", "select", "upload",
    "eval", "extract", "wait", "get",
    "switch", "back", "scroll", "screenshot",
    "cookies", "close-tab",
})

_VALUED_GLOBAL_FLAGS = frozenset({
    "--profile", "--session", "--cdp-url", "--browser",
    "-p", "-s", "-b",
})


def _is_option_value(parts: list[str], index: int) -> bool:
    """parts[index+1] 是不是 parts[index] 这个 flag 的值."""
    if index + 1 >= len(parts):
        return False
    return not parts[index + 1].startswith("-")


def parse_browser_command(command: str) -> tuple[list[str], str | None]:
    """拆出 [global_flags...], action. action 是第一个不以 - 开头的 token.

    返回 (global_args, action_or_none). 引号不匹配时返回 ([], None) 让上层兜底.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return [], None

    global_args: list[str] = []
    action: str | None = None
    i = 0
    while i < len(parts):
        part = parts[i]
        if not part.startswith("-"):
            action = part
            break
        global_args.append(part)
        if part in _VALUED_GLOBAL_FLAGS and _is_option_value(parts, i):
            i += 1
            global_args.append(parts[i])
        i += 1

    return global_args, action


def extract_session_config(global_args: list[str]) -> StoredSessionConfig:
    """从 global flags 抽出 session 五件套 (headed / profile / cdp_url / connect / browser).

    --session 故意不抽 — session 名由调用方算 (固定 = pl-{sid}), 不允许 LLM 覆盖.
    """
    config = StoredSessionConfig()
    i = 0
    while i < len(global_args):
        part = global_args[i]
        if part == "--headed":
            config.headed = True
        elif part == "--profile" and _is_option_value(global_args, i):
            config.profile = global_args[i + 1]
            i += 1
        elif part == "--cdp-url" and _is_option_value(global_args, i):
            config.cdp_url = global_args[i + 1]
            i += 1
        elif part == "--connect":
            config.connect = True
        elif part == "--browser" and _is_option_value(global_args, i):
            config.browser = global_args[i + 1]
            i += 1
        i += 1
    return config


def build_session_args(
    command: str,
    *,
    sandbox: Path,
    session_name: str,
) -> tuple[list[str], StoredSessionConfig | None]:
    """算出最终要前置到 CLI 命令的 session 参数 list + 命令成功后应落盘的 config.

    返回值: (cli_args, config_to_save_after_success).
      - cli_args 永远立刻可用
      - config_to_save_after_success 为 None 表示"命令完成后无需落盘"
        (close action 不存; 其它 action 等 caller 在子进程成功后再 save)

    open: 强制 headed=True; effective = stored ∪ explicit; 成功后落盘.
    close: 只回 --session NAME, 不存盘 (不重放 profile / cdp 等, 否则 CLI 拒).
    其它动作: 强制 headed=True; 用 stored ∪ explicit 重放; 成功后落盘.
    """
    global_args, action = parse_browser_command(command)
    explicit_config = extract_session_config(global_args)

    if action == "close":
        return ["--session", session_name], None

    stored_config = load_session_config(sandbox)
    effective = stored_config.merged_with(explicit_config)

    if action == "open":
        effective.headed = True
    elif action in _REPLAY_ACTIONS:
        effective.headed = True

    args = effective.to_cli_args(session_name)

    # 仅当 effective 非空 (有任何 session 标志) 才提交给 caller 存盘.
    # 全空时返回 None, caller 也不会动磁盘 — 避免空 config 覆盖.
    has_any = (
        effective.headed
        or effective.profile
        or effective.cdp_url
        or effective.connect
        or effective.browser
    )
    return args, (effective if has_any else None)


def normalize_eval_command(command: str) -> str:
    """`eval Array.from(document.querySelectorAll(...))` 走 shlex.split 会被切碎.

    策略:
      - 不是 eval 命令 → 不动
      - JS payload 已被匹配的同种引号完整包围 → 不动
      - 其它情况 → 整段 shlex.quote, 当成单参数传
    """
    stripped = command.strip()
    if not stripped.startswith("eval "):
        return command
    payload = stripped[5:].strip()
    if not payload:
        return command
    if len(payload) >= 2 and payload[0] == payload[-1] and payload[0] in {'"', "'"}:
        return f"eval {payload}"
    return f"eval {shlex.quote(payload)}"


_SCREENSHOT_PATTERN = re.compile(r"^screenshot(\s+--full)?(?:\s+(.+))?$")


def transform_screenshot_path(command: str, sandbox: Path) -> str:
    """browser-use CLI 不读 shell cwd, 相对路径会丢. 落到 sandbox 下绝对路径.

    `screenshot` (无 path, 返回 base64) → 不动
    `screenshot foo.png` → `screenshot <sandbox>/foo.png`
    `screenshot /abs.png` → 不动
    `screenshot --full foo.png` → `screenshot --full <sandbox>/foo.png`
    """
    match = _SCREENSHOT_PATTERN.match(command.strip())
    if not match:
        return command
    full_flag = match.group(1) or ""
    path_arg = match.group(2)
    if not path_arg:
        return command
    path = Path(path_arg).expanduser()
    if not path.is_absolute():
        path = sandbox / path
    return f"screenshot{full_flag} {path}"


def prepare_browser_command(command: str, sandbox: Path) -> str:
    """跑命令前必经的字符串规范化."""
    command = normalize_eval_command(command)
    command = transform_screenshot_path(command, sandbox)
    return command


def is_state_command(command: str) -> bool:
    """跳过 global flags 取首个 action token. 不 shlex.split — 不需要那么严."""
    for part in command.strip().split():
        if part.startswith("-"):
            continue
        return part == "state"
    return False


def is_switch_command(command: str) -> bool:
    _, action = parse_browser_command(command)
    return action == "switch"


def extract_action_verb(command: str) -> str | None:
    """给 HITL allowlist_key 用 — 同一 verb 会话内只审一次. None 表示拿不到."""
    _, action = parse_browser_command(command)
    return action
