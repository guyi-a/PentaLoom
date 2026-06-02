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
from pentaloom.tools.computer_use import COMPUTER_USE_FULL_NAME
from pentaloom.tools.files import FILE_READ_FULL_NAME, FILE_VERIFY_FULL_NAME
from pentaloom.tools.python_env import (
    INSTALL_LIBS_FULL_NAME,
    RUN_SCRIPT_FULL_NAME,
)
from pentaloom.tools.search import WEB_SEARCH_FULL_NAME
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
    "- **决策前置**: 任务只是\"找信息 / 查事实 / 看新闻 / 取最新数据\" → 先用 "
    f"{WEB_SEARCH_FULL_NAME}, 别为了找信息开浏览器. 浏览器只在以下场景才用: "
    "(a) 用户明确说\"打开 X 网站 / 登录 / 点 X / 截图 X\"; (b) 搜索返回不足或需要看完整页面 "
    "(把 href 给 browser_bridge open_tab 而不是开新搜索); (c) 必须在网页里交互 (填表/提交/下载).\n"
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

SEARCH_PROMPT_INSTRUCTIONS: str = (
    "### 联网信息 — 三档分工 (search → fetch → browser)\n"
    "\n"
    "**第一档: 找链接 / 抓摘要** — `web_search`\n"
    f"- 找信息 / 查事实 / 看新闻 / 取最新数据 / 查文档 — 一律先调 {WEB_SEARCH_FULL_NAME}"
    "(query=..., region=...), 返 [{title, href, body}, ...]. 这是浏览器的**上游**, 别跳过它直接开浏览器搜.\n"
    "- **region 三档** (默认 both, 拿不准就 both):\n"
    "  - `cn` (Bocha 国内源): 明显只关国内 — 国内政策 / 中国本土公司 / 国内活动 / "
    "中文文学 / 国内法规 / 中国地名地物等.\n"
    "  - `global` (Tavily 海外源): 明显只关海外 — OpenAI/Anthropic/Google 等海外公司 / "
    "英文论文 / 海外政策 / 国际赛事英文报道等.\n"
    "  - `both` (两路并发合并, 默认): 跨域话题 / 双边都重要 — AI/科技/全球新闻/"
    "跨境业务/对比国内外做法等. 无把握时默认走 both, 比单边漏掉重要源安全.\n"
    "\n"
    "**第二档: 读完整页 / 抽特定信息** — `WebFetch`\n"
    "- search 摘要不够, 需要看完整文章 / API 文档 / blog 全文 — 用 WebFetch(url=..., prompt=...). "
    "url 必须是 http/https. prompt 写'抽什么', 例: '总结这篇文章的核心论点', '列出 API 的所有 endpoint'.\n"
    "- WebFetch 内部跑小模型抽信息, 适合静态页 / Markdown / HTML — 不能登录, 不能跑 JS, 不能截图.\n"
    "- 典型链: web_search 拿 href → 选 1-3 个相关的 → WebFetch 各读一遍 → 综合.\n"
    "- 首次弹审批, Allow session 后整会话所有 WebFetch 免审.\n"
    "\n"
    "**第三档: 交互式操作** — browser_bridge / browser_use (见浏览器自动化段)\n"
    "- 需要登录, 需要点击 / 填表 / 跑 JS, 需要截图, 需要看 SPA 渲染结果 — 才上浏览器.\n"
    "- 别为了'读静态页' 开浏览器, WebFetch 更快更省.\n"
    "\n"
    "**决策树**:\n"
    "```\n"
    "找信息 / 查事实           → web_search\n"
    "  摘要够答              → 直接答, 不读全文\n"
    "  摘要不够 / 要读全文     → WebFetch(url, prompt='抽什么')\n"
    "需要登录 / 点击 / 截图     → browser_bridge\n"
    "用户明说'打开 X 网站'    → browser_bridge (操作意图明确)\n"
    "PDF 类直链             → Read (本地 PDF) 或 download → file_read\n"
    "```\n"
    "\n"
    f"**不要调** {WEB_SEARCH_FULL_NAME} 的时候:\n"
    "  - 用户明确\"打开 X 网站\" / \"登录\" / \"在 X 上点 Y\" — 直接走浏览器.\n"
    "  - 任务是操作页面 (填表 / 提交 / 截图特定 URL) 而非找信息.\n"
    f"- {WEB_SEARCH_FULL_NAME} 报错 (如 key 未配 / 限流) 或返空 → 退到浏览器手动搜.\n"
    "- 首次调用弹审批, Allow session 后整会话所有搜索免审."
)

COMPUTER_PROMPT_INSTRUCTIONS: str = (
    "### macOS 桌面自动化 (computer-use)\n"
    "- 桌面级任务 (操作原生 app / 跨 app 走菜单 / 发系统快捷键 / Electron 主区兜底) 走 "
    f"{COMPUTER_USE_FULL_NAME}. 先 load `computer-use` skill 看铁律.\n"
    "- **第一步**调 action='permissions' 检查**两类**权限: "
    "accessibility (给 AX/mouse/key) + screen_recording (给 screenshot). "
    "缺哪个按返回 message 引导用户开 (是两个独立 TCC, 同一宿主分别授权).\n"
    "- 任何 app 都能走 menu (action='menu', target=app名, path=['文件','新建']) — "
    "Electron app 菜单完整可用, 这是最稳的入口.\n"
    "- **AX 主区拿不到** (Electron 主区 / Chrome 网页区 / Canvas) 时走视觉兜底: "
    "screenshot(target=app) → 推理元素逻辑坐标 → mouse_click(x, y) + paste(text). "
    "screenshot **直接返 image 给你 see** (~1.1k vision token), 不是 base64 字符串 — "
    "**不要**写脚本解码 + Read, 工具已经把图直接给你了.\n"
    "- **坐标系**: mouse/paste 用逻辑像素; screenshot 返物理像素 + 缩放后像素 + scale; "
    "换算公式 logical_x = image_x * (logical_w / scaled_w), 直接照 ScreenshotResult.note 抄.\n"
    "- 输文本一律用 paste(text=...), **不要尝试 type unicode** (中文 IME 会吞键码). "
    "paste 工具自动备份 + 恢复用户剪贴板, 不会污染.\n"
    "- 首次任何 action 弹审批, Allow session 后整个会话所有 computer 调用免审."
)

# capabilities.render 收的就是这个 list. 顺序决定段在 system prompt 里出现的顺序.
# search 放在 browser 前面, 强调"找信息先搜, 浏览器是兜底"的决策优先级.
# weaver 放最后, 是元能力 — 用其他能力做事的过程中沉淀方法论.
from pentaloom.prompts.weaver import WEAVER_PROMPT_INSTRUCTIONS

TOOL_PROMPT_INSTRUCTIONS: list[str] = [
    WORKSPACE_PROMPT_INSTRUCTIONS,
    FILES_PROMPT_INSTRUCTIONS,
    PYTHON_ENV_PROMPT_INSTRUCTIONS,
    SEARCH_PROMPT_INSTRUCTIONS,
    BROWSER_PROMPT_INSTRUCTIONS,
    COMPUTER_PROMPT_INSTRUCTIONS,
    WEAVER_PROMPT_INSTRUCTIONS,
]

__all__ = [
    "BROWSER_PROMPT_INSTRUCTIONS",
    "COMPUTER_PROMPT_INSTRUCTIONS",
    "FILES_PROMPT_INSTRUCTIONS",
    "PYTHON_ENV_PROMPT_INSTRUCTIONS",
    "SEARCH_PROMPT_INSTRUCTIONS",
    "TOOL_PROMPT_INSTRUCTIONS",
    "WEAVER_PROMPT_INSTRUCTIONS",
    "WORKSPACE_PROMPT_INSTRUCTIONS",
]
