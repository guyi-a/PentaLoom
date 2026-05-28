"""启用的 skill 列表 — 透传给 ClaudeAgentOptions.skills.

SDK 原生 skills 走 .claude/skills/<name>/SKILL.md, 我们这里只维护启用清单.
加新 skill:
  1. mkdir agent/.claude/skills/<name> && 写 SKILL.md (YAML front-matter
     必含 name + description)
  2. 把 "<name>" 加进 ENABLED_SKILLS
  3. 重启即可, 不改 prompts/ 任何代码.

机制: app.py 设 setting_sources=["project"] + skills=<list>, CLI 从 sandbox cwd
往上找 .claude/skills/ 命中 agent/.claude/skills/. SDK 自动把 Skill(<name>) 加进
allowed_tools, LLM 在需要时主动调 Skill 工具拉取 SKILL.md 全文 — 不会在 system
prompt 里硬塞, 按需加载省 token.
"""

from __future__ import annotations

# 启用 skill 名匹配 SKILL.md 的 name / 目录名.
# 空列表时 app.py 会把 skills=None 传给 ClaudeAgentOptions, 跟 SDK 默认一致.
ENABLED_SKILLS: list[str] = [
    "report-generator",
]
