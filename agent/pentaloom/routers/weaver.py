"""Weaver HTTP API — sidebar / 设置页面读产物用. 工具调用走 SDK in-process MCP, 不走这.

M14 阶段实装:
  GET /weaver/products  → {skills, subagents, workflows, apps} 一次拉全, sidebar 用.
  GET /weaver/skills    → 仅 skills 列表 (含内置 + 用户织的), 兼容性单独开.

M16 加: apps 真返数据 (含 invocation_count + 是否有 app.json + components 摘要).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pentaloom.capabilities.weaver import app as app_biz
from pentaloom.capabilities.weaver import index, skill
from pentaloom.config import get_settings

router = APIRouter(prefix="/weaver", tags=["weaver"])


class SkillSummary(BaseModel):
    name: str
    description: str
    source: str  # "builtin" | "agent_woven" | "user_imported" | "user_handwritten"


class AppSummary(BaseModel):
    """Sidebar 用的 app 摘要. 不返完整 manifest (太大), 关键 metric 够展示."""

    name: str
    description: str
    source: str
    status: str  # draft | ready | dirty | failed (递进式 weave 状态机, GPT 收口)
    invocation_count: int
    has_app_definition: bool
    component_counts: dict[str, int]  # {scripts: 2, windows: 1, ...}


class WeaverProductsResponse(BaseModel):
    skills: list[SkillSummary]
    subagents: list[Any] = []   # M17 实装
    workflows: list[Any] = []   # M16 workflow milestone
    apps: list[AppSummary]


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


def _collect_apps() -> list[AppSummary]:
    """Sidebar 用户 weave 的 app 列表. 单个 app 的 manifest 读失败不阻塞全列表,
    log warn + skip (manifest 损坏的 app 也会在 list 里露出, 但 component_counts
    全 0; 用户从 list 里看出哪个坏)."""
    settings = get_settings()
    idx = index.load_index(settings)
    out: list[AppSummary] = []
    for e in idx.apps:
        try:
            manifest = app_biz.read_manifest(settings, e.name)
            app_def = app_biz.read_app_definition(settings, e.name)
            meta = app_biz.read_meta(settings, e.name)
        except index.WeaverError:
            # 损坏 app 也展示 (零 count), 不让一个挂的 app 屏蔽整个 list
            out.append(AppSummary(
                name=e.name, description=e.description, source=e.source,
                status="failed",  # 读不出来当 failed
                invocation_count=0, has_app_definition=False, component_counts={},
            ))
            continue
        counts: dict[str, int] = {}
        if app_def is not None:
            counts = {
                "windows": len(app_def.components.windows),
                "services": len(app_def.components.services),
                "scripts": len(app_def.components.scripts),
                "schedules": len(app_def.components.schedules),
                "watches": len(app_def.components.watches),
            }
        out.append(AppSummary(
            name=e.name, description=e.description, source=e.source,
            status=meta.status if meta else "draft",
            invocation_count=len(manifest.invocations),
            has_app_definition=app_def is not None,
            component_counts=counts,
        ))
    return out


@router.get("/skills", response_model=list[SkillSummary])
def list_skills() -> list[SkillSummary]:
    return _collect_skills()


@router.get("/products", response_model=WeaverProductsResponse)
def list_products() -> WeaverProductsResponse:
    """sidebar 一次拉全部产物. M14/M16 阶段 subagents/workflows 仍空."""
    return WeaverProductsResponse(
        skills=_collect_skills(),
        apps=_collect_apps(),
    )


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


@router.get("/apps/{name}/manifest", response_model=dict)
def read_app_manifest(name: str) -> dict:
    """Sidebar 详情 / 后续 invoke UI 用 — 返完整 manifest + components + files 列表."""
    settings = get_settings()
    entry = index.find_entry(settings, "app", name)
    if entry is None:
        raise HTTPException(404, f"app not found: {name}")
    try:
        return app_biz.manifest_invocations_summary(settings, name)
    except index.WeaverError as e:
        raise HTTPException(500, str(e)) from e
