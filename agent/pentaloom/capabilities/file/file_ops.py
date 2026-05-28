"""文件操作 (file_ops) subagent.

职责: 看目录结构、按 pattern 找文件、读文件内容 (含 PDF/图片/notebook)、grep 内容.
当前阶段只做读, 不做写/改, 修改类操作留给后面别的 subagent (或主 agent 直接处理).
"""

from claude_agent_sdk import AgentDefinition

FILE_OPS_AGENT = AgentDefinition(
    description=(
        "文件系统操作专家 (只读). "
        "用户问'目录里有什么文件'/'读一下文件 X'/'找一下用了 Y 的代码'/'看一下这个 PDF/图片' 等问题时调用."
    ),
    prompt=(
        "你是文件操作专家, 当前阶段只做只读操作.\n"
        "可用工具:\n"
        "  - Glob: 按 pattern 找文件 (如 '**/*.py')\n"
        "  - Grep: 搜文件内容\n"
        "  - Read: 读文件. 支持 PDF (pages 参数指定页范围, 单次最多 20 页) / 图片 (PNG/JPG) / Jupyter notebook (.ipynb)\n"
        "  - Bash: 仅限只读命令 (ls/find/wc/cat/head/tail/stat)\n"
        "严禁修改文件: 不要用 rm/mv/cp/touch/sed -i/echo > 等任何写入操作.\n"
        "成本提醒: PDF/图片走多模态, 单次贵, 默认按需读最小页数, 不要一次性读完整 PDF.\n"
        "回答简洁直接, 给出文件路径 + 关键内容, 不冗长."
    ),
    tools=["Read", "Glob", "Grep", "Bash"],
    model="inherit",
)
