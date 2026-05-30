"""LoomPool _build() 启动时调 assemble_weaver(), 返主 agent ClaudeAgentOptions 要的两个东西:

  - subagent_defs: dict[name, AgentDefinition] (跟内置 agents 合并)
  - skill_names:   list[str]                   (跟 ENABLED_SKILLS 合并)

副作用: 把每个 skill symlink 到 data_dir/.claude/skills/<name>/, SDK 爬升能找到
(Spike 3 extras Test 1 verified).

M14 阶段 subagent_defs 永远是空 (subagent UI 配置 M17 才实装).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from pentaloom.capabilities.weaver import index, paths
from pentaloom.config import Settings


async def assemble_weaver(
    settings: Settings,
) -> tuple[dict[str, Any], list[str]]:
    """读 weaver/index.json, sync skill symlinks, 返 (subagent_defs, skill_names).

    异步签名是为了跟 LoomPool._build (async) 调用一致, 当前实现纯同步 I/O.
    """
    paths.ensure_dirs(settings)
    idx = index.load_index(settings)

    skill_names: list[str] = []
    for entry in idx.skills:
        try:
            index.sync_skill_symlink(settings, entry.name)
            skill_names.append(entry.name)
        except index.WeaverError as e:
            # 单个 skill 损坏不阻塞整个启动
            logger.warning(f"skill {entry.name} symlink 失败, skip: {e}")

    # subagent 部分 M17 实装. 留 dict 接口给 LoomPool 直接 merge.
    subagent_defs: dict[str, Any] = {}

    if skill_names:
        logger.info(f"assemble_weaver: 加载 {len(skill_names)} weaver skill(s)")
    return subagent_defs, skill_names
