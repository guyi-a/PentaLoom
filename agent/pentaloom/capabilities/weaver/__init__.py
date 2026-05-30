"""自进化能力 — agent 沉淀方法论 / 管理自己产物库.

M14 范围: Skill 沉淀 + 6 个 meta-tool (list / inspect / edit / delete / run / tail_logs).
Workflow / Subagent / App 留给 M16-M18.
"""

from pentaloom.capabilities.weaver.index import WeaverError
from pentaloom.capabilities.weaver.loader import assemble_weaver
from pentaloom.capabilities.weaver.models import (
    IndexEntry,
    SkillFrontmatter,
    SkillMeta,
    WeaverIndex,
    WeaverKind,
    WeaverSource,
)

__all__ = [
    "IndexEntry",
    "SkillFrontmatter",
    "SkillMeta",
    "WeaverError",
    "WeaverIndex",
    "WeaverKind",
    "WeaverSource",
    "assemble_weaver",
]
