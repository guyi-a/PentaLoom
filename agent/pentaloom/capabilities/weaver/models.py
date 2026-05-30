"""weaver 数据模型. M14 只用 Skill 那一套; Subagent / Workflow / App 留 stub.

不进 SQLAlchemy — M14 文件 + index.json 是单一 source of truth (设计文档 §7.5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

WeaverKind = Literal["skill", "subagent", "workflow", "app"]
WeaverSource = Literal["agent_woven", "user_imported", "user_handwritten"]


class SkillFrontmatter(BaseModel):
    """SKILL.md YAML front-matter 必填三件套."""

    name: str
    description: str
    when_to_use: str | None = None


class SkillMeta(BaseModel):
    """weaver/skills/<name>/meta.json — 跟 SKILL.md 互补的运行时元数据."""

    name: str
    kind: Literal["skill"] = "skill"
    description: str
    source: WeaverSource = "agent_woven"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = None
    use_count: int = 0
    is_trusted: bool = False


class IndexEntry(BaseModel):
    """weaver/index.json 里每一项 — 主 agent prompt assemble 时一次性看清单."""

    name: str
    kind: WeaverKind
    description: str
    path: str  # 相对 data_dir/weaver/ 的路径
    source: WeaverSource = "agent_woven"


class WeaverIndex(BaseModel):
    """weaver/index.json 全文. 四类产物分桶."""

    skills: list[IndexEntry] = Field(default_factory=list)
    subagents: list[IndexEntry] = Field(default_factory=list)
    workflows: list[IndexEntry] = Field(default_factory=list)
    apps: list[IndexEntry] = Field(default_factory=list)

    def bucket(self, kind: WeaverKind) -> list[IndexEntry]:
        return getattr(self, f"{kind}s")
