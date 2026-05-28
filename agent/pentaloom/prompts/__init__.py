"""PentaLoom 主 agent 提示词组装.

五段式: identity → style → capabilities (工具 + 可加载 skills) → env (运行时字体清单) →
context (mounted_dirs). 空段省略.

调用入口:
    from pentaloom.prompts import assemble_main_prompt
    prompt = assemble_main_prompt(mounted_dirs=[...])

工具引导走 `pentaloom.tools.TOOL_PROMPT_INSTRUCTIONS` (每个 tool 模块自己维护).
Skill 内容由 SDK 原生机制按需加载 (Skill 工具) — system prompt 里只列 ENABLED_SKILLS
的名字 + description, 不再硬塞全文.
"""

from __future__ import annotations

from pathlib import Path

from pentaloom.prompts import capabilities, context, env, fingerprint
from pentaloom.prompts.identity import MAIN_IDENTITY
from pentaloom.prompts.skills import ENABLED_SKILLS
from pentaloom.prompts.style import GLOBAL_STYLE


def assemble_main_prompt(
    *,
    mounted_dirs: list[str | Path] | None = None,
    skills: list[str] | None = None,
    tool_instructions: list[str] | None = None,
    skill_instructions: list[str] | None = None,
    available_cjk_fonts: list[str] | None = None,
) -> str:
    """主 agent system prompt. 空段省略, 段间 \\n\\n 分隔.

    显式传参用于测试; 默认从注册表 / 系统探测取.
    """
    if tool_instructions is None:
        # 延迟 import 防循环 (tools/__init__.py 不 import prompts/, 反向同样).
        from pentaloom.tools import TOOL_PROMPT_INSTRUCTIONS

        tool_instructions = list(TOOL_PROMPT_INSTRUCTIONS)
    if skills is None:
        skills = list(ENABLED_SKILLS)
    if skill_instructions is None:
        # SDK 原生 skill 机制接通后, 不再内联 skill 全文 — capabilities 段只列
        # ENABLED_SKILLS 名字让 LLM 自己按需 Skill() 拉取.
        skill_instructions = []
    if available_cjk_fonts is None:
        # 延迟 import 防 prompts → infra 循环 (infra/__init__.py 不 import prompts).
        from pentaloom.infra.fonts import detect_cjk_fonts

        available_cjk_fonts = detect_cjk_fonts()

    sections: list[tuple[str, str]] = [
        ("identity", MAIN_IDENTITY.strip()),
        ("style", GLOBAL_STYLE.strip()),
        (
            "capabilities",
            capabilities.render(
                tool_instructions, skills, skill_instructions
            ).strip(),
        ),
        (
            "env",
            env.render_runtime_env(available_cjk_fonts=available_cjk_fonts).strip(),
        ),
        ("context", context.render_workspace(mounted_dirs).strip()),
    ]
    rendered = [(name, text) for name, text in sections if text]
    prompt = "\n\n".join(text for _, text in rendered)
    fingerprint.log(prompt, [name for name, _ in rendered])
    return prompt


__all__ = ["assemble_main_prompt", "ENABLED_SKILLS"]
