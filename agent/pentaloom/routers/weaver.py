"""Weaver HTTP API — sidebar / 设置页面读产物用. 工具调用走 SDK in-process MCP, 不走这.

M14 阶段实装:
  GET /weaver/products  → {skills, subagents, workflows, apps} 一次拉全, sidebar 用.
  GET /weaver/skills    → 仅 skills 列表 (含内置 + 用户织的), 兼容性单独开.

M16+ 会扩 POST/PUT/DELETE (用户在 UI 直接编辑产物), 现在用户只在对话里通过 agent 改.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pentaloom.capabilities.weaver import index, skill
from pentaloom.config import get_settings

router = APIRouter(prefix="/weaver", tags=["weaver"])


class SkillSummary(BaseModel):
    name: str
    description: str
    source: str  # "builtin" | "agent_woven" | "user_imported" | "user_handwritten"


class WeaverProductsResponse(BaseModel):
    skills: list[SkillSummary]
    subagents: list[Any] = []   # M17 实装
    workflows: list[Any] = []   # M16 实装
    apps: list[Any] = []        # M18 实装


def _collect_skills() -> list[SkillSummary]:
    """Sidebar 只看用户产物 — 内置 skill (report-generator 等) 是 PentaLoom 出厂能力,
    不能改 / 不能删, 在 sidebar 显示反而是噪音. agent 端 list_weaver / inspect_weaver
    仍然支持读内置 (防 agent weave 重名), 但 frontend 这条只走用户产物.
    """
    settings = get_settings()
    idx = index.load_index(settings)
    return [
        SkillSummary(name=e.name, description=e.description, source=e.source)
        for e in idx.skills
    ]


@router.get("/skills", response_model=list[SkillSummary])
def list_skills() -> list[SkillSummary]:
    return _collect_skills()


@router.get("/products", response_model=WeaverProductsResponse)
def list_products() -> WeaverProductsResponse:
    """sidebar 一次拉全部产物. M14 阶段 subagents/workflows/apps 永远是空 list."""
    return WeaverProductsResponse(skills=_collect_skills())


@router.get("/skills/{name}/content", response_model=dict)
def read_skill_content(name: str) -> dict:
    """读单个 skill 的完整 SKILL.md 内容. 内置 + 用户织的都支持读 (agent 端 inspect_weaver
    也走这条; sidebar 现在不显示 builtin, 但 agent 想 inspect 内置还是可以)."""
    settings = get_settings()
    builtin = next(
        (b for b in skill.builtin_skills_summary() if b["name"] == name), None
    )
    if builtin is not None:
        from pentaloom.capabilities.weaver import paths
        skill_md = paths.builtin_skill_md(name)
        return {
            "name": name,
            "source": "builtin",
            "description": builtin["description"],
            "content": skill_md.read_text(),
        }
    entry = index.find_entry(settings, "skill", name)
    if entry is None:
        raise HTTPException(404, f"skill not found: {name}")
    try:
        content = skill.read_skill_md(settings, name)
    except index.WeaverError as e:
        raise HTTPException(500, str(e)) from e
    return {
        "name": name,
        "source": entry.source,
        "description": entry.description,
        "content": content,
    }
