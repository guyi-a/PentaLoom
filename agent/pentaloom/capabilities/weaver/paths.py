"""weaver/ 目录路径辅助. 单 entry point 算路径, 避免散落 string 拼.

物理布局 (Spike 3 extras Test 1 + 2 锁定):
    data_dir/
    ├── weaver/skills/<name>/SKILL.md      物理位置 (用户 vscode 直接看)
    ├── weaver/index.json                  产物总索引
    ├── weaver/.trash/<name>-<ts>/         软删的旧产物
    ├── .claude/skills/<name>/             symlink → ../weaver/skills/<name>/
    └── sandboxes/<sid>/                   cwd, SDK 爬 2 层命中 ../.claude/skills/
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pentaloom.config import Settings


def weaver_root(settings: Settings) -> Path:
    return settings.data_dir / "weaver"


def skills_dir(settings: Settings) -> Path:
    return weaver_root(settings) / "skills"


def skill_dir(settings: Settings, name: str) -> Path:
    return skills_dir(settings) / name


def skill_md(settings: Settings, name: str) -> Path:
    return skill_dir(settings, name) / "SKILL.md"


def skill_meta(settings: Settings, name: str) -> Path:
    return skill_dir(settings, name) / "meta.json"


def trash_dir(settings: Settings) -> Path:
    return weaver_root(settings) / ".trash"


def trash_target(settings: Settings, kind: str, name: str) -> Path:
    """软删目标路径: weaver/.trash/<kind>-<name>-<ts>/. ts 带秒精度, 避免同名碰撞."""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return trash_dir(settings) / f"{kind}-{name}-{ts}"


def index_json(settings: Settings) -> Path:
    return weaver_root(settings) / "index.json"


def repo_root() -> Path:
    """PentaLoom 仓库根目录. 用来定位内置 SKILL.md (在 repo-root .claude/skills/<name>/).

    注意 parents 层数 — 这个文件在 capabilities/weaver/, 上溯 4 层才到 repo root.
    """
    return Path(__file__).resolve().parents[4]


def builtin_skill_md(name: str) -> Path:
    return repo_root() / ".claude" / "skills" / name / "SKILL.md"


def skills_link_root(settings: Settings) -> Path:
    """SDK 爬升能找到的 skill 镜像目录. data_dir 在 sandbox 父父目录,
    SDK 从 sandboxes/<sid>/ 爬 2 层正好命中这里 (Spike 3 extras Test 1 verified)."""
    return settings.data_dir / ".claude" / "skills"


def skill_symlink(settings: Settings, name: str) -> Path:
    return skills_link_root(settings) / name


def ensure_dirs(settings: Settings) -> None:
    """启动 / weave_* 时确保骨架目录存在. mkdir 幂等."""
    weaver_root(settings).mkdir(parents=True, exist_ok=True)
    skills_dir(settings).mkdir(parents=True, exist_ok=True)
    trash_dir(settings).mkdir(parents=True, exist_ok=True)
    skills_link_root(settings).mkdir(parents=True, exist_ok=True)
