"""启用的 skill 列表 — 透传给 ClaudeAgentOptions.skills.

SDK 原生 skills 走 .claude/skills/<name>/SKILL.md, 我们这里只维护启用清单.
加新 skill:
  1. mkdir <project_root>/.claude/skills/<name> && 写 SKILL.md (YAML front-matter
     必含 name + description)
  2. 把 "<name>" 加进 EXPLICIT_SKILLS (design-* 家族自动 glob, 不用手写)
  3. 重启即可, 不改 prompts/ 任何代码.

机制: app.py 设 setting_sources=["project"] + skills=<list>, CLI 从 sandbox cwd
往上找 .claude/skills/ 命中 <project_root>/.claude/skills/. SDK 自动把 Skill(<name>)
加进 allowed_tools, LLM 在需要时主动调 Skill 工具拉取 SKILL.md 全文 — 不会在
system prompt 里硬塞, 按需加载省 token.
"""

from __future__ import annotations

from pathlib import Path

# 项目根: agent/pentaloom/prompts/skills.py → .parent x 4
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SKILLS_DIR = _PROJECT_ROOT / ".claude" / "skills"


def _discover_design_skills() -> list[str]:
    """扫 .claude/skills/design-*/ 自动启用 design-picker + 43 个 design-* 风格库.

    design-* 家族经常增减 (从 krow 抄过来后续可能再补充), 写死会让加 skill 时
    忘改这里. import-time 算一次, 重启后才会 pick up 新增 — 跟 SDK skill loading
    本身就需要重启的语义一致.
    """
    if not _SKILLS_DIR.exists():
        return []
    return sorted(
        d.name
        for d in _SKILLS_DIR.iterdir()
        if d.is_dir() and d.name.startswith("design-")
    )


# 显式列举的非 design 家族 skill (不走自动 glob 因为它们各自独立, 不能整族启停).
# 空列表时 app.py 会把 skills=None 传给 ClaudeAgentOptions, 跟 SDK 默认一致.
#
# 注意 .claude/ 整目录 gitignored — 内置 skill 文件不入 git, 别的 dev clone
# 仓库后这里列的 skill 会 SDK warn skip (不挂, 但缺能力). 长期方案: 把内置
# skill 挪到入 git 的位置 (e.g., agent/builtin_skills/), 启动 sync 到 .claude/.
EXPLICIT_SKILLS: list[str] = [
    "report-generator",
    "browser-use",
    "browser-bridge",
    "computer-use",
    # Invocable App skills:
    "app-generator",
    "app-window",
    "app-service",
    "app-patterns",
]

# 最终 ENABLED_SKILLS = 显式 + 所有 design-* (含 design-picker 路由器)
ENABLED_SKILLS: list[str] = [*EXPLICIT_SKILLS, *_discover_design_skills()]
