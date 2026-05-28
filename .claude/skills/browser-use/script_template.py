"""可复用浏览器自动化脚本模板 — 把 PentaLoom 交互探索好的流程固化成独立 Python.

用法 (LLM 生成时):
  1. 把下面的 BROWSER_SESSION / BROWSER_PROFILE / COOKIE_FILE / PYTHON_ENV 四个常量
     从 browser_use_session_info() 工具拿到的值替换进去 (绝对值, 不要算).
  2. 主流程改成你实际探索好的步骤. 元素操作用 stable selector (id / data-* / aria-*),
     不要硬编码 state 工具给出的瞬态 index — 那东西 DOM 一动就失效.
  3. 写完 run_python_script 跑一次自验证, blocking_count = 0 (没异常) 才能交付.
  4. 失败别盲改 — 看 stderr 走 SKILL 的 recovery 流程.

约束:
  - 子进程调 browser-use CLI, 让 session 命名能跟 PentaLoom 那次复用同一个 Chrome
  - 失败立刻 raise, 不要静默吞异常 — 静默吞会让用户以为脚本跑了, 实际啥也没做
  - 不主动 close 浏览器 — 留着供下次跑 / 用户自己看, cookies 自动重导出
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# ── 常量 ───────────────────────────────────────────────────────
# 这四个值通过 browser_use_session_info() 工具拿到, 写死.
# 不要在脚本里再去算 — 那样脚本就跟 sandbox 状态强耦合, 跨机器跑不了.

BROWSER_SESSION = "pl-XXXX-REPLACE"  # session_info.session_name
BROWSER_PROFILE: str | None = None  # session_info.profile (有传, 没有就 None)
COOKIE_FILE = "/abs/path/to/session.cookies.json"  # session_info.cookies_path
PYTHON_ENV = "/Users/.../pentaloom-data/python-env"  # PentaLoom 共享 uv project

# 是否显示浏览器窗口. 后台跑改 False (省资源, 但部分网站会反爬).
HEADED = True

# ── 子进程 helper ──────────────────────────────────────────────


def bu(*args: str, timeout: int = 120) -> str:
    """跑一条 browser-use 命令, 返回 stdout+stderr. 非 0 退出抛 RuntimeError.

    所有 session 标志 (--headed / --session / --profile) 由 helper 注入, 调用方
    只传 action + action-args. 例:
        bu("open", "https://example.com")
        bu("click", "5")
    """
    cmd = ["uv", "run", "--project", PYTHON_ENV, "browser-use"]
    if HEADED:
        cmd.append("--headed")
    cmd += ["--session", BROWSER_SESSION]
    if BROWSER_PROFILE:
        cmd += ["--profile", BROWSER_PROFILE]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        raise RuntimeError(
            f"browser-use {args[0] if args else '?'} 失败 (exit={r.returncode}):\n"
            f"{out[-2000:]}"
        )
    return out


def eval_js(script: str) -> str:
    """跑一段 JS, 返回它的 return 值字符串. script 应该以 'return ...' 起."""
    return bu("eval", script)


def ensure_cookies() -> None:
    """首次运行把 PentaLoom 探索时的登录 cookies 灌进去. 已有则 browser-use 自处理."""
    if not Path(COOKIE_FILE).exists():
        return
    try:
        bu("cookies", "import", COOKIE_FILE)
    except RuntimeError as e:
        # cookies 已经在 / 格式微差异都不阻塞主流程, 真正登录态丢了主流程会自己暴
        print(f"[warn] cookies 导入跳过: {e}")


# ── 主流程 ─────────────────────────────────────────────────────


def main() -> None:
    ensure_cookies()

    # 1. 打开目标页
    bu("open", "https://example.com")

    # 2. 等关键元素就绪 (避免页面还在 loading 就抓数据)
    # 用 eval 而不是 state 的 index — index 不稳, JS selector 稳.
    eval_js("return document.querySelector('main') !== null")

    # 3. 抽数据 (示例: 取页面标题)
    title = eval_js("return document.title")
    print(f"标题: {title.strip()}")

    # 4. 不主动 close — 浏览器留着, 下次跑 / 用户自己看都行.
    #    想强制关掉改成: bu("close")


if __name__ == "__main__":
    main()
