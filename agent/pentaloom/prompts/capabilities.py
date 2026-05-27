"""把 tools / skills 的引导段拼成"工具守则"."""

from __future__ import annotations


def render(
    tool_instructions: list[str],
    skill_names: list[str],
    skill_instructions: list[str] | None = None,
) -> str:
    """渲染 capabilities 段.

    tool_instructions: 各工具模块导出的 PROMPT_INSTRUCTIONS — 已是完整一段
        (含开头小标题), 直接拼.
    skill_names: 走 SDK skill 加载机制的 skill 名 (M4+ 接上时用; M3 暂为空).
    skill_instructions: 内联在 system prompt 里的 skill 段 (M3 临时方案,
        SDK skill 加载机制接上后挪走). 每条已含"### Skill: xxx"小标题.
    """
    parts: list[str] = ["## 工具守则"]
    parts.extend(s.strip() for s in tool_instructions if s and s.strip())

    if skill_instructions:
        parts.append("## Skills (内置)")
        parts.extend(s.strip() for s in skill_instructions if s and s.strip())

    if skill_names:
        names = ", ".join(skill_names)
        parts.append(
            f"### Skills (按需加载)\n"
            f"以下 skill 已启用: {names}. 命中相关任务时调用 Skill 工具加载具体内容."
        )

    return "\n\n".join(parts)
