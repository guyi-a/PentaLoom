"""Shell 命令"无害分类"工具.

is_harmless_command 是公开 API: 命令只读 / 只看系统状态 → True; 写文件 / 装包 /
跑脚本 / 任何不在白名单里的 → False (保守默认).

支持复合命令拆分 (&& / || / ; / |) — 任一段非无害, 整体 False.
支持 transparent wrapper 剥离 (sudo / env / timeout / nice / nohup) — 看真正
的命令.
"""
from __future__ import annotations

import re

# 命令本身不需要 workspace 也不会改任何状态: 看文件系统 / 看系统信息 /
# 看进程 / 网络诊断 / 文本处理 (在 pipe 里安全). 这些命令在 auto / full_access
# 模式下静默放行, 不再弹审批.
_WORKSPACE_FREE_COMMANDS = frozenset({
    # filesystem read
    "ls", "cat", "head", "tail", "less", "more", "tree", "bat",
    "find", "grep", "egrep", "fgrep", "rg", "ag", "fd",
    # file metadata
    "stat", "file", "wc", "du", "df", "readlink", "realpath",
    "md5", "md5sum", "shasum", "sha256sum", "cksum", "b2sum",
    # binary inspection
    "xxd", "od", "hexdump", "strings",
    # system info
    "uname", "hostname", "whoami", "id", "uptime", "arch",
    "sw_vers", "sysctl", "system_profiler",
    "free", "vmstat", "iostat", "dmesg",
    # process / port
    "ps", "top", "htop", "lsof", "pgrep", "fuser",
    # network diagnostics
    "ping", "dig", "nslookup", "host", "traceroute", "mtr",
    "ifconfig", "ip", "netstat", "ss", "route", "arp",
    # path / env lookup
    "which", "whereis", "whence", "type", "command",
    "pwd", "dirname", "basename", "echo", "printf",
    "date", "cal", "env", "printenv", "locale",
    # shell builtins / no-ops
    "sleep", "true", "false", "test", "[",
    # text processing (safe in pipes)
    "sort", "uniq", "cut", "tr", "awk", "jq", "yq",
    "column", "paste", "expand", "unexpand", "fold", "fmt",
    "diff", "comm", "cmp",
    # sed — 特判: sed -i / --in-place 写文件不无害
    "sed",
    # archive listing
    "zipinfo",
    # macOS utilities
    "open", "pbcopy", "pbpaste",
    # man / help
    "man", "help", "info", "apropos",
    # version queries — 解释器特判 (--version / -V / version)
    "python3", "python", "node", "ruby", "go", "java", "rustc",
    # git read-only — 子命令特判
    "git",
    # download / fetch — curl/wget 特判 (GET-only 才安全)
    "curl", "wget", "http", "httpie",
    # xargs — 看内层命令决定
    "xargs",
})

# 不改变内层命令语义的 wrapper, 剥掉看真正的 command.
_TRANSPARENT_WRAPPERS = frozenset({
    "sudo", "nohup", "nice", "timeout", "env", "command",
})

# 脚本解释器 — 跑文件时内容未知, 默认要审 (除非是 --version 这种).
_SCRIPT_INTERPRETERS = frozenset({
    "python", "python3", "python2",
    "node", "ruby", "perl", "lua",
    "bash", "sh", "zsh", "fish", "dash",
    "deno", "bun", "tsx", "ts-node", "npx",
    "Rscript",
})

_VERSION_FLAG_STRICT = frozenset({"--version", "-V", "version"})
_VERSION_FLAG_LOOSE = frozenset({"--version", "-V", "-v", "version"})
# python -v 是 verbose mode 不是 version; node -v 是 version. python 走严格匹配.
_STRICT_VERSION_INTERPRETERS = frozenset({"python", "python3", "python2"})

_GIT_READ_ONLY_SUBCOMMANDS = frozenset({
    "log", "diff", "status", "show", "branch", "tag",
    "stash", "remote", "config", "describe", "shortlog",
    "blame", "ls-files", "ls-tree", "cat-file", "rev-parse",
    "rev-list", "reflog", "worktree",
    "grep", "diff-tree", "name-rev", "for-each-ref",
})

# curl/wget 出现这些 flag 就有写副作用 (上传 / 改方法 / 写本地文件).
# -o = curl 写本地, -O = wget 写本地 (大小写都收).
_CURL_WRITE_FLAGS = frozenset({
    "-X", "--request",
    "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
    "-F", "--form",
    "-T", "--upload-file",
    "-o", "-O", "--output",
    "--json",
})


def split_command_segments(command: str) -> list[list[str]]:
    """把复合 shell 命令拆成 token list. 按 && / || / ; 切, 再按 | 切,
    每段都是单一命令.
    """
    segments: list[list[str]] = []
    for sub in re.split(r'\s*(?:&&|\|\||;)\s*', command.strip()):
        for part in re.split(r'\s*\|\s*', sub):
            tokens = part.strip().split()
            if tokens:
                segments.append(tokens)
    return segments


def unwrap_command(tokens: list[str]) -> tuple[str, list[str]]:
    """剥掉 transparent wrapper, 返 (实际命令, 剩余参数).

    e.g. ["sudo", "env", "ls", "-la"] → ("ls", ["-la"])
    """
    i = 0
    while i < len(tokens) and tokens[i] in _TRANSPARENT_WRAPPERS:
        i += 1
        # 跳过 wrapper 自己的参数, 比如 env VAR=val / timeout 30
        while i < len(tokens) and (
            "=" in tokens[i]
            or (tokens[i].startswith("-") and tokens[i - 1] in ("timeout", "nice"))
            or (tokens[i - 1] == "timeout" and tokens[i].replace(".", "").isdigit())
        ):
            i += 1
    if i >= len(tokens):
        return "", []
    return tokens[i], tokens[i + 1:]


def _is_version_query(cmd_name: str, args: list[str]) -> bool:
    if cmd_name not in _SCRIPT_INTERPRETERS or len(args) != 1:
        return False
    flags = _VERSION_FLAG_STRICT if cmd_name in _STRICT_VERSION_INTERPRETERS else _VERSION_FLAG_LOOSE
    return args[0] in flags


def _is_git_read_only(args: list[str]) -> bool:
    if not args:
        return True  # bare `git` 只 print help
    sub = args[0]
    if sub.startswith("-"):
        return True  # git --version / git --help
    return sub in _GIT_READ_ONLY_SUBCOMMANDS


def _is_safe_http_command(cmd_name: str, args: list[str]) -> bool:
    """curl/wget 是不是只读 GET (不带写副作用 flag)."""
    if cmd_name not in ("curl", "wget", "http", "httpie"):
        return False
    return not any(flag in args for flag in _CURL_WRITE_FLAGS)


def _segment_needs_workspace(tokens: list[str]) -> bool:
    cmd, args = unwrap_command(tokens)
    if not cmd:
        return False

    if cmd == "xargs":
        if args:
            return _segment_needs_workspace(args)
        return False

    if cmd in _SCRIPT_INTERPRETERS:
        if _is_version_query(cmd, args):
            return False
        return True

    if cmd == "git":
        return not _is_git_read_only(args)

    if cmd in ("curl", "wget", "http", "httpie"):
        return not _is_safe_http_command(cmd, args)

    if cmd == "sed":
        return "-i" in args or "--in-place" in args

    return cmd not in _WORKSPACE_FREE_COMMANDS


def command_needs_workspace(command: str) -> bool:
    """命令是否会改文件 / 跑脚本 / 装包 等需要 workspace 的事.

    返 False = 仅检查 (filesystem / 系统 / 进程 / 网络) 状态, 任意目录跑都安全.
    返 True = 脚本执行 / 写 / 装包 / 不认识的命令 (保守默认).
    """
    segments = split_command_segments(command)
    if not segments:
        return False
    return any(_segment_needs_workspace(seg) for seg in segments)


def is_harmless_command(command: str) -> bool:
    """命令是否安全到可以跳过用户审批.

    用于 ApprovalPolicy 的 auto / full_access 模式: 只读 / 检查类 (ls / ps /
    grep / git log 等) 直接放行, 不弹弹窗.
    """
    return not command_needs_workspace(command)
