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
from pentaloom.tools.browser_bridge import BROWSER_BRIDGE_FULL_NAME
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
    "- 任何浏览器任务**先决策走哪条路径**: 调 "
    f"{BROWSER_BRIDGE_FULL_NAME}(action='extension_status'), 看返回 `ready` 字段:\n"
    "  - `ready=true` → load `browser-bridge` skill, 走真实浏览器 (扩展桥接). **首选**.\n"
    "  - `ready=false` → load `browser-use` skill, 走独立机器人浏览器 (CLI 子进程).\n"
    f"- bridge 路径: 通过 {BROWSER_BRIDGE_FULL_NAME}(action=...) 单工具, 27 个 action "
    "(open_tab / read_state / click N / type N text / describe_element 等). "
    "首次任何 action 弹审批, Allow session 后整个会话所有 bridge 调用免审.\n"
    "- use 路径: 先调 "
    f"{INSTALL_BROWSER_USE_FULL_NAME}(step='check') 看环境, 按 next_step 装齐 (install → chromium). "
    f"跑命令用 {BROWSER_USE_FULL_NAME}(command='...'), 命令体跟 browser-use CLI 一致.\n"
    f"- 生成可复用 Python 脚本前调 {BROWSER_SESSION_INFO_FULL_NAME}() 拿 session_name / "
    "profile / cookies_path 当常量 (仅 use 路径需要)."
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
