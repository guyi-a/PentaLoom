"""主 agent 的工具引导段集中处.

历史上每个 tools/<x>.py 自己导出 X_PROMPT_INSTRUCTIONS, 散在工具模块里. 现在收编到
prompts/ 下统一维护 — 工具模块只关心行为, 给 LLM 的引导文字归 prompts/ 管.

依赖方向: prompts/tools.py 单向 import 工具模块的 FULL_NAME 常量 (那些是工具标识
的事实来源, 也方便 IDE 跳转); 工具模块不反向 import 本文件, 避免循环.

每段格式约定: "### 小标题\\n- 要点1\\n- 要点2..." — capabilities.render 直接拼到
"## 工具守则" 大标题下.
"""

from __future__ import annotations

from pentaloom.tools.browser import (
    BROWSER_SESSION_INFO_FULL_NAME,
    BROWSER_USE_FULL_NAME,
    INSTALL_BROWSER_USE_FULL_NAME,
)
from pentaloom.tools.files import FILE_READ_FULL_NAME, FILE_VERIFY_FULL_NAME
from pentaloom.tools.python_env import (
    INSTALL_LIBS_FULL_NAME,
    RUN_SCRIPT_FULL_NAME,
)
from pentaloom.tools.workspace import FULL_TOOL_NAME as REQUEST_WORKSPACE_DIR_FULL_NAME

WORKSPACE_PROMPT_INSTRUCTIONS: str = (
    "### 工作区挂载\n"
    "- 用户提到本地目录 / 项目, 但当前没在已授权目录里时, 调 "
    f"{REQUEST_WORKSPACE_DIR_FULL_NAME} 请求挂载, 别自己猜路径或用 Bash 直接读.\n"
    "- 挂载在用户同意的下一轮 turn 才生效, 当前 turn 内仍是旧的文件系统权限."
)

FILES_PROMPT_INSTRUCTIONS: str = (
    "### 文件读写\n"
    f"- 读 .docx / .pptx / .xlsx 用 {FILE_READ_FULL_NAME}, "
    "不要自己写 Python 脚本提取二进制 (会丢格式 / 漏内容 / 慢).\n"
    "- 读 .pdf / .txt / .md / .py / 图片 / .ipynb 走 Read 工具 (SDK 内置, 多模态自动处理).\n"
    "- 生成或修改 .pdf / .pptx 后, 必须调一次 "
    f"{FILE_VERIFY_FULL_NAME}(path, autofix=True) 自检质量, "
    "直到 blocking_count=0 才能向用户报告交付完成."
)

PYTHON_ENV_PROMPT_INSTRUCTIONS: str = (
    "### 跑 Python 代码\n"
    f"- 装第三方包用 {INSTALL_LIBS_FULL_NAME} (会请求用户授权), "
    "不要自己拼 pip / uv 命令走 Bash.\n"
    f"- 跑 .py 脚本用 {RUN_SCRIPT_FULL_NAME}: 先用 Write 把脚本落到 sandbox 或挂载目录, "
    "再把绝对路径传过来. 工具不接受 inline 代码 (escape 灾难 + 行号对不上).\n"
    "- 一次性、纯命令的探查走 Bash; 涉及导入 / 多行逻辑 / 调试的一律走脚本路径."
)

BROWSER_PROMPT_INSTRUCTIONS: str = (
    "### 浏览器自动化\n"
    "- 任何浏览器任务**先 load `browser-use` skill**, 看完铁律 (操作成功 ≠ 任务成功 / "
    "用户阻断终止 / URL 未变换标签页 / state 索引瞬态 / 下载工作流) 再开始.\n"
    f"- 第一次用浏览器先调 {INSTALL_BROWSER_USE_FULL_NAME}(step='check') 看环境是否就绪, "
    "按返回 next_step 装齐 (install → chromium).\n"
    f"- 跑命令用 {BROWSER_USE_FULL_NAME}(command='...'), 命令体跟 browser-use CLI 一致 "
    "(open / state / click N / eval ... / close); --session 不需要手传, 工具自动注入.\n"
    f"- 生成可复用 Python 脚本前调 {BROWSER_SESSION_INFO_FULL_NAME}() 拿 session_name / "
    "profile / cookies_path 当常量."
)

# capabilities.render 收的就是这个 list. 顺序决定段在 system prompt 里出现的顺序.
TOOL_PROMPT_INSTRUCTIONS: list[str] = [
    WORKSPACE_PROMPT_INSTRUCTIONS,
    FILES_PROMPT_INSTRUCTIONS,
    PYTHON_ENV_PROMPT_INSTRUCTIONS,
    BROWSER_PROMPT_INSTRUCTIONS,
]

__all__ = [
    "BROWSER_PROMPT_INSTRUCTIONS",
    "FILES_PROMPT_INSTRUCTIONS",
    "PYTHON_ENV_PROMPT_INSTRUCTIONS",
    "TOOL_PROMPT_INSTRUCTIONS",
    "WORKSPACE_PROMPT_INSTRUCTIONS",
]
