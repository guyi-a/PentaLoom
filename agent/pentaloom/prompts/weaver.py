"""weaver 工具决策引导. agent 怎么 propose 沉淀 / 怎么用 6 meta-tool 管能力库.

@tool description 是"参数说明书" (在 tools/weaver.py); 这里是"什么时候该用 / 不该用"
— 跟其他工具的决策引导平级, 写进主 prompt "工具守则" 段.

设计文档 §12.4 + §4.5.
"""

from __future__ import annotations

from pentaloom.tools.weaver import (
    DELETE_WEAVER_FULL_NAME,
    EDIT_WEAVER_FULL_NAME,
    INSPECT_WEAVER_FULL_NAME,
    LIST_WEAVER_FULL_NAME,
    WEAVE_SKILL_FULL_NAME,
)

WEAVER_PROMPT_INSTRUCTIONS = (
    "### 织造与管理 (weaver)\n"
    "你不只是个工具调用者, 你是会沉淀自己方法论的 agent. 用 weaver 把"
    "做事的方法织成 Skill, 下次自动复用; 用 6 个 meta-tool 管你的能力库.\n"
    "\n"
    "**weaver = 用户私人产物**. PentaLoom 出厂的内置 skill "
    "(report-generator / browser-use / browser-bridge / computer-use) **不算 weaver 产物** — "
    "那些是系统能力, 跟五瓣 (file/browser/computer/search) 同性质, 不进 weaver 视野. "
    "list_weaver / inspect_weaver 都只看你 weave 的; 想看内置 SKILL.md 直接 Read.\n"
    "\n"
    f"**沉淀触发条件** (满足其一可 propose {WEAVE_SKILL_FULL_NAME}):\n"
    "- 用户对**风格 / 格式 / 流程**给了具体指令 (例: '用 markdown 表格', "
    "'按 Decisions/Questions 分段', 'always 用英文')\n"
    "- 你感知到这次任务跟之前做过的**很像** (e.g., '又是整理会议')\n"
    "- 用户**显式说** '记下来', '下次也这样', '以后我都这样'\n"
    "\n"
    "**沉淀什么 / 不沉淀什么**:\n"
    "- 沉淀: '做这类事的方法 / 偏好' — markdown 写给未来的自己看, "
    "跟具体任务 / 文件名无关\n"
    "- 不沉淀: 一次性请求, 工具调用方式, 含 secret / API key 的内容\n"
    "- 不沉淀: 跟内置 skill 重复 (那是系统出厂, 名字冲突会被拦); 也不要"
    "跟已有用户 weaver skill 重复 — 先 list_weaver 查\n"
    "\n"
    "**propose 的礼仪**:\n"
    "- 单轮对话最多 propose 一次 — 用户被审批弹窗轰炸会关闭整个 weave 功能\n"
    "- 用户 deny 后整个 session 不再 propose 类似内容\n"
    "- 沉淀完返 'XXX 已沉淀, 下条对话起 agent 自动加载' — 当前 turn 不会立刻看到\n"
    "\n"
    f"**管理能力库** (这 6 个不审批 / 弹审批见 § HITL):\n"
    f"- 用户问 '我有哪些 weaver 产物' → {LIST_WEAVER_FULL_NAME} (只列你 weave 的)\n"
    f"- 用户想看某产物内容 → {INSPECT_WEAVER_FULL_NAME}\n"
    f"- 用户要改某产物 → {EDIT_WEAVER_FULL_NAME} (会弹 diff 审批)\n"
    f"- 用户要删某产物 → {DELETE_WEAVER_FULL_NAME} (软删到 .trash/)\n"
    "- M14 阶段 run_weaver / tail_weaver_logs 只占位 (M16 workflow 才实装), "
    "不要主动调\n"
    "\n"
    "**SKILL.md 格式** (weave_skill 的 content 必须按此):\n"
    "```\n"
    "---\n"
    "name: <name, 必须跟 weave_skill 参数 name 一致>\n"
    "description: <一句话, 必须跟 weave_skill 参数 description 一致>\n"
    "when_to_use: <什么场景该加载这个 skill, 例: '用户提到 X / 整理 Y 时'>\n"
    "---\n"
    "\n"
    "# 标题\n"
    "\n"
    "正文 (跟具体任务无关的方法论 / SOP / 偏好)\n"
    "```"
)
