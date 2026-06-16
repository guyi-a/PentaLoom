"""沉淀 + 删 Skill 的业务逻辑. 不碰 tool 注册 — 纯写盘 + index.

weave_skill 流程:
  1. 校验 markdown frontmatter (必有 name / description; when_to_use 可选但推荐)
  2. 校验名字冲突 (跨 kind 不重名)
  3. 写 SKILL.md + meta.json 到 weaver/skills/<name>/
  4. 加进 index.json
  5. symlink 到 data_dir/.claude/skills/<name>/ (供 agent 加载)

delete 是软删: 整个 skill 目录搬到 .trash/.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from pentaloom.capabilities.weaver import index, paths
from pentaloom.capabilities.weaver.models import (
    IndexEntry,
    SkillFrontmatter,
    SkillMeta,
    WeaverSource,
)
from pentaloom.config import Settings

_VALID_NAME = "abcdefghijklmnopqrstuvwxyz0123456789-"


def _validate_name(name: str) -> str:
    """skill name 必须 kebab-case (小写字母 / 数字 / 短横线), 跟目录 + SKILL.md frontmatter 三处一致.

    防 path injection (../) + 跟 skill 加载约定对齐.
    """
    name = name.strip()
    if not name:
        raise index.WeaverError("skill name 不能为空")
    if not all(c in _VALID_NAME for c in name):
        raise index.WeaverError(
            f"skill name 只能小写字母 / 数字 / 短横线: {name!r}"
        )
    if len(name) > 64:
        raise index.WeaverError(f"skill name 太长 (>64): {name!r}")
    return name


def _parse_frontmatter(content: str) -> tuple[SkillFrontmatter, str]:
    """从 SKILL.md 顶部 --- YAML --- 块解 frontmatter, 返回 (frontmatter, body).

    skill loader 要求 SKILL.md 必有 frontmatter, 我们提前校验避免写盘后才报错.
    """
    if not content.startswith("---\n"):
        raise index.WeaverError(
            "SKILL.md 必须以 YAML frontmatter 开头 (--- ... ---)"
        )
    try:
        _, fm_text, body = content.split("---\n", 2)
    except ValueError as e:
        raise index.WeaverError(f"SKILL.md frontmatter 闭合失败: {e}") from e
    try:
        fm_data = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        raise index.WeaverError(f"SKILL.md frontmatter YAML 解析失败: {e}") from e
    try:
        fm = SkillFrontmatter.model_validate(fm_data)
    except Exception as e:
        raise index.WeaverError(f"SKILL.md frontmatter 缺字段: {e}") from e
    return fm, body


def weave_skill(
    settings: Settings,
    name: str,
    description: str,
    content: str,
    *,
    source: WeaverSource = "agent_woven",
) -> SkillMeta:
    """沉淀一个 skill. 返回 SkillMeta 写盘后的快照.

    content 是完整 SKILL.md (含 frontmatter). description 必须跟 frontmatter
    一致 (双校验, 避免 LLM 在两处填不同的). name 同样.
    """
    name = _validate_name(name)
    description = description.strip()
    if not description:
        raise index.WeaverError("description 不能为空")

    fm, _body = _parse_frontmatter(content)
    if fm.name != name:
        raise index.WeaverError(
            f"name 跟 frontmatter.name 不一致: arg={name!r} fm={fm.name!r}"
        )
    if fm.description.strip() != description:
        raise index.WeaverError(
            "description 跟 frontmatter.description 不一致 — 两处应写同一句"
        )

    # 冲突检查: weaver 跨 kind 不重名 + 不能跟内置 skill 同名
    occupied = index.name_exists_any_kind(settings, name)
    if occupied is not None:
        raise index.WeaverError(
            f"名字已被占用 (kind={occupied}, name={name!r}). 用 edit_weaver 改, "
            "或者换个名字"
        )
    # 延迟 import 避循环
    from pentaloom.prompts.skills import ENABLED_SKILLS
    if name in ENABLED_SKILLS:
        raise index.WeaverError(
            f"名字 {name!r} 跟内置 skill 冲突. 内置 skill (report-generator / "
            "browser-use 等) 是 PentaLoom 出厂能力, 换个名字"
        )

    # 写盘
    paths.ensure_dirs(settings)
    skill_dir = paths.skill_dir(settings, name)
    skill_dir.mkdir(parents=True, exist_ok=False)

    paths.skill_md(settings, name).write_text(content)

    meta = SkillMeta(
        name=name,
        description=description,
        source=source,
        created_at=datetime.utcnow(),
    )
    paths.skill_meta(settings, name).write_text(meta.model_dump_json(indent=2))

    # index + symlink
    index.upsert_entry(
        settings,
        IndexEntry(
            name=name,
            kind="skill",
            description=description,
            path=f"skills/{name}/",
            source=source,
        ),
    )
    index.sync_skill_symlink(settings, name)
    logger.info(f"weaved skill: {name}")
    return meta


def edit_skill(
    settings: Settings, name: str, new_content: str
) -> SkillMeta:
    """改 SKILL.md 全文. frontmatter.name 不允许改 (改名 = 删 + 新 weave)."""
    name = _validate_name(name)
    entry = index.find_entry(settings, "skill", name)
    if entry is None:
        raise index.WeaverError(f"skill 不存在: {name}")

    fm, _ = _parse_frontmatter(new_content)
    if fm.name != name:
        raise index.WeaverError(
            f"edit 不允许改 name. 想改名请 delete_weaver + weave_skill"
        )

    # 写新内容; description 跟着 frontmatter 走 (允许通过 edit 改描述)
    paths.skill_md(settings, name).write_text(new_content)

    meta_path = paths.skill_meta(settings, name)
    if meta_path.exists():
        meta = SkillMeta.model_validate_json(meta_path.read_text())
    else:
        meta = SkillMeta(name=name, description=fm.description, source="agent_woven")
    meta.description = fm.description.strip()
    meta_path.write_text(meta.model_dump_json(indent=2))

    if entry.description != fm.description.strip():
        index.upsert_entry(
            settings,
            IndexEntry(
                name=name,
                kind="skill",
                description=fm.description.strip(),
                path=f"skills/{name}/",
                source=entry.source,
            ),
        )
    logger.info(f"edited skill: {name}")
    return meta


def delete_skill_soft(settings: Settings, name: str) -> Path | None:
    """软删: 整个目录搬到 weaver/.trash/skill-<name>-<ts>/, 同时撤 symlink 跟 index.

    若物理目录已不存在 (孤儿条目: index 有但目录被外部清掉了), 只清 index entry +
    symlink, 返 None — 不抛错, 避免调用方绕过 meta-tool 直接改 index.
    """
    name = _validate_name(name)
    entry = index.find_entry(settings, "skill", name)
    if entry is None:
        raise index.WeaverError(f"skill 不存在: {name}")

    src = paths.skill_dir(settings, name)
    if not src.exists():
        # 孤儿条目: 撤 symlink + 清 index, 不搬 trash (没东西可搬)
        index.remove_skill_symlink(settings, name)
        index.remove_entry(settings, "skill", name)
        logger.info(f"deleted orphan skill entry (no physical dir): {name}")
        return None

    index.remove_skill_symlink(settings, name)
    index.remove_entry(settings, "skill", name)

    paths.trash_dir(settings).mkdir(parents=True, exist_ok=True)
    dst = paths.trash_target(settings, "skill", name)
    shutil.move(str(src), str(dst))
    logger.info(f"deleted (soft) skill: {name} → {dst}")
    return dst


def read_skill_md(settings: Settings, name: str) -> str:
    p = paths.skill_md(settings, name)
    if not p.exists():
        raise index.WeaverError(f"skill SKILL.md 不存在: {p}")
    return p.read_text()


def read_skill_meta(settings: Settings, name: str) -> SkillMeta | None:
    p = paths.skill_meta(settings, name)
    if not p.exists():
        return None
    return SkillMeta.model_validate_json(p.read_text())


def builtin_skills_summary() -> list[dict[str, Any]]:
    """读 ENABLED_SKILLS + 各自 SKILL.md frontmatter, 返 agent list_weaver 用的清单.

    内置 skill 物理位置在 repo-root .claude/skills/<name>/SKILL.md, 跟 weaver/skills/
    分开 (内置随仓库升级, weaver 是用户产物). sidebar 不展示内置 — 这个函数只给
    agent 端 list_weaver / inspect_weaver 用 (agent 需要知道全清单防 weave 重名).
    """
    # 延迟 import 避 weaver loader 启动顺序问题
    from pentaloom.prompts.skills import ENABLED_SKILLS

    out: list[dict[str, Any]] = []
    for name in ENABLED_SKILLS:
        md_path = paths.builtin_skill_md(name)
        if not md_path.exists():
            continue
        try:
            fm, _ = _parse_frontmatter(md_path.read_text())
            out.append({
                "name": name,
                "description": fm.description,
                "source": "builtin",
            })
        except index.WeaverError as e:
            logger.warning(f"内置 skill {name} frontmatter 解析失败: {e}")
    return out
