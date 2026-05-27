"""启用的 skill 列表 — 透传给 ClaudeAgentOptions.skills.

SDK 原生 skills 走 .claude/skills/<name>/SKILL.md, 我们这里只维护启用清单.
M3 框架阶段空列表 — 真要加 skill 时:
  1. mkdir .claude/skills/<name> && 写 SKILL.md
  2. 把 "<name>" 加进 ENABLED_SKILLS
  3. 重启即可, 不改 prompts/ 任何代码.
"""

from __future__ import annotations

# 启用 skill 名匹配 SKILL.md 的 name / 目录名, 或 plugin:skill 形式.
# 空列表时 app.py 会把 skills=None 传给 ClaudeAgentOptions, 跟 SDK 默认一致.
ENABLED_SKILLS: list[str] = []
