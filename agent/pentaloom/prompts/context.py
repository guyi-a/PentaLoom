"""运行时上下文段 — 当前会话的挂载目录等."""

from __future__ import annotations

from pathlib import Path


def render_workspace(mounted_dirs: list[str | Path] | None) -> str:
    """渲染 mounted_dirs 段. 空列表 → 返回空串 (调用方过滤掉空段).

    SDK 的 add_dirs 只是给这些路径开读写白名单, LLM 本身并不知道这些目录代表
    "用户的项目". 不加这段, 用户问"介绍下这个项目"时 LLM 没有工作区上下文, 会
    fallback 去 memory / 训练数据里乱搜, 答非所问.
    """
    if not mounted_dirs:
        return ""
    lines = "\n".join(f"- {d}" for d in mounted_dirs)
    return (
        "## 当前工作区\n"
        f"用户已授权访问以下目录:\n{lines}\n"
        "当用户提到\"这个项目\"/\"当前项目\"/\"仓库\"/\"代码\"等, 默认指上述目录. "
        "需要查看代码或文件时, 直接到这些目录里找, 不要去其它地方."
    )
