"""Destructive 操作识别 — 跨所有审批模式永远拦截.

is_destructive 是公开 API. 即使用户开了 full_access, 这些命令仍要弹审批.
设计原则: 偏严不偏松 — 用户多点一次按钮 vs 文件被 rm -rf 不可恢复, 永远选前者.
"""
from __future__ import annotations

import re
from typing import Any


# Bash / 脚本里出现这些命令名 → 危险.
_DESTRUCTIVE_SHELL_COMMANDS = frozenset({
    "rm", "rmdir", "unlink", "shred",
    "kill", "killall", "pkill",
})

# 这些 wrapper 会把内层命令拽进来, 必须看后续 token 有没有危险命令.
# e.g. xargs rm / sudo rm / find -exec rm / bash -c "rm ..."
_COMMAND_WRAPPERS = frozenset({
    "xargs", "sudo", "nohup", "nice", "timeout", "env",
    "bash", "sh", "zsh",
    "find",
})

# git 危险子命令: 强制 push / 重置 hard / clean / 删分支.
_GIT_DESTRUCTIVE_PATTERN = re.compile(
    r'git\s+'
    r'(?:clean|reset\s+--hard|push\s+.*--force|branch\s+.*-[dD])\b'
)


def is_destructive(tool_name: str, tool_input: dict[str, Any]) -> bool:
    """工具调用是不是破坏性操作.

    PentaLoom 现在 Bash 是唯一会跑任意 shell 命令的工具, 所以 tool_name = "Bash"
    时拆 command. 其他工具 (browser_use / computer_use / weave_*) 不当 destructive
    判断 — 它们各自的破坏性边界另有 HITL 流程.
    """
    if tool_name != "Bash":
        return False

    cmd = (tool_input.get("command") or "").strip()
    if not cmd:
        return False

    # 拆 && / || / ; 再拆 | — 任一段命中就 destructive.
    for sub_cmd in re.split(r'\s*(?:&&|\|\||;)\s*', cmd):
        for part in re.split(r'\s*\|\s*', sub_cmd):
            tokens = part.strip().split()
            if not tokens:
                continue
            if tokens[0] in _DESTRUCTIVE_SHELL_COMMANDS:
                return True
            # wrapper 后面藏着 destructive 命令: xargs rm / sudo rm / find -exec rm
            # 内层 token 可能被引号包住 (e.g. bash -c 'rm -rf /tmp'), strip 后再比.
            if tokens[0] in _COMMAND_WRAPPERS and any(
                t.strip("'\"") in _DESTRUCTIVE_SHELL_COMMANDS for t in tokens[1:]
            ):
                return True

    if _GIT_DESTRUCTIVE_PATTERN.search(cmd):
        return True

    return False