"""路径基本校验 helper — 绝对路径 + 文件存在 + 是文件.

为什么不做 mounted_dirs 白名单 (跟 plan 略有偏差):
  - 现存 in-process 工具 (install_python_libs / run_python_script) 也都不做.
  - 真要做需要把 session_id 注入 @tool 装饰函数 (走 ContextVar 或 per-session
    server), 引入新机制. M2 先对齐现状, M3 统一给所有 in-process 工具加白名单.
  - 当前安全模型: SDK 子进程的 add_dirs 拘束 Read/Write/Bash; in-process 工具
    完全信任 agent. 这跟 Anthropic Claude Code 默认 trust-the-agent 模型一致.
"""

from __future__ import annotations

from pathlib import Path


def resolve_user_path(raw: str) -> Path:
    """把 agent 传的 path 标准化为绝对路径, 校验存在且是文件.

    抛 ValueError 表示路径有问题 (空 / 不存在 / 非文件) — 工具层捕获后
    映射成 is_error=True 的 tool_result, agent 看到能自己改正.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("path 不能为空")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ValueError(f"path 必须是绝对路径: {raw!r}")
    try:
        p = p.resolve(strict=True)
    except FileNotFoundError as e:
        raise ValueError(f"path 不存在: {raw!r}") from e
    if not p.is_file():
        raise ValueError(f"path 不是文件: {raw!r}")
    return p
