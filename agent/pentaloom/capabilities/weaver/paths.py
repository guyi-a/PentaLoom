"""weaver/ 目录路径辅助. 单入口算路径, 避免散落 string 拼接.

物理布局:
    data_dir/
    ├── weaver/skills/<name>/SKILL.md      物理位置 (用户 vscode 直接看)
    ├── weaver/index.json                  产物总索引
    ├── weaver/.trash/<name>-<ts>/         软删的旧产物
    ├── .claude/skills/<name>/             symlink → ../weaver/skills/<name>/
    └── sandboxes/<sid>/                   agent cwd, 向上查找可命中 skill 镜像
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
    """agent 从 sandbox 向上查找时能命中的 skill 镜像目录."""
    return settings.data_dir / ".claude" / "skills"


def skill_symlink(settings: Settings, name: str) -> Path:
    return skills_link_root(settings) / name


# ─── Invocable App 路径 ─────────────────────────────────────────────────────

def apps_dir(settings: Settings) -> Path:
    return weaver_root(settings) / "apps"


def app_dir(settings: Settings, name: str) -> Path:
    return apps_dir(settings) / name


def app_manifest(settings: Settings, name: str) -> Path:
    return app_dir(settings, name) / "manifest.json"


def app_definition(settings: Settings, name: str) -> Path:
    return app_dir(settings, name) / "app.json"


def app_meta(settings: Settings, name: str) -> Path:
    return app_dir(settings, name) / "meta.json"


def app_index_html(settings: Settings, name: str) -> Path:
    return app_files_dir(settings, name) / "index.html"


def app_main_js(settings: Settings, name: str) -> Path:
    """LLM 填的 handler JS. framework 加载它后注册 invocation."""
    return app_files_dir(settings, name) / "app.js"


def app_files_dir(settings: Settings, name: str) -> Path:
    return app_dir(settings, name) / "files"


def app_assets_dir(settings: Settings, name: str) -> Path:
    return app_files_dir(settings, name) / "assets"


def app_logs_dir(settings: Settings, name: str) -> Path:
    return app_dir(settings, name) / "logs"


def app_runs_dir(settings: Settings, name: str) -> Path:
    """每次 invocation 输出落盘到 runs/<run_id>/. 大对象用 artifact_path 引,
    不塞 base64 进 agent context."""
    return app_dir(settings, name) / "runs"


def app_run_dir(settings: Settings, name: str, run_id: str) -> Path:
    return app_runs_dir(settings, name) / run_id


def ensure_app_dirs(settings: Settings, name: str) -> None:
    """weave_app 时一次性建好骨架. mkdir 幂等."""
    app_dir(settings, name).mkdir(parents=True, exist_ok=True)
    app_files_dir(settings, name).mkdir(parents=True, exist_ok=True)
    app_assets_dir(settings, name).mkdir(parents=True, exist_ok=True)
    app_logs_dir(settings, name).mkdir(parents=True, exist_ok=True)
    app_runs_dir(settings, name).mkdir(parents=True, exist_ok=True)


# ─── Workflow 路径 ──────────────────────────────────────────────────────────
# 跟 app 同 weaver_root 平级, 但物理位置独立 — workflow 没 files/ 子树, 只有
# workflow.json 主文件 + meta + 独立 logs/runs.jsonl + runs/<run_id>/.

def workflows_dir(settings: Settings) -> Path:
    return weaver_root(settings) / "workflows"


def workflow_dir(settings: Settings, name: str) -> Path:
    return workflows_dir(settings) / name


def workflow_json(settings: Settings, name: str) -> Path:
    """主文件 — WorkflowDefinition 序列化, 用户 vscode 直接看."""
    return workflow_dir(settings, name) / "workflow.json"


def workflow_meta(settings: Settings, name: str) -> Path:
    return workflow_dir(settings, name) / "meta.json"


def workflow_logs_dir(settings: Settings, name: str) -> Path:
    """独立 logs/runs.jsonl, 不混 app log."""
    return workflow_dir(settings, name) / "logs"


def workflow_runs_dir(settings: Settings, name: str) -> Path:
    """每次 invoke_workflow 输出落 runs/<run_id>/, 跟 app_runs_dir 同款."""
    return workflow_dir(settings, name) / "runs"


def workflow_run_dir(settings: Settings, name: str, run_id: str) -> Path:
    return workflow_runs_dir(settings, name) / run_id


def ensure_workflow_dirs(settings: Settings, name: str) -> None:
    """weave_workflow 时一次性建好骨架. mkdir 幂等."""
    workflow_dir(settings, name).mkdir(parents=True, exist_ok=True)
    workflow_logs_dir(settings, name).mkdir(parents=True, exist_ok=True)
    workflow_runs_dir(settings, name).mkdir(parents=True, exist_ok=True)


def ensure_dirs(settings: Settings) -> None:
    """启动 / weave_* 时确保骨架目录存在. mkdir 幂等."""
    weaver_root(settings).mkdir(parents=True, exist_ok=True)
    skills_dir(settings).mkdir(parents=True, exist_ok=True)
    apps_dir(settings).mkdir(parents=True, exist_ok=True)
    workflows_dir(settings).mkdir(parents=True, exist_ok=True)
    trash_dir(settings).mkdir(parents=True, exist_ok=True)
    skills_link_root(settings).mkdir(parents=True, exist_ok=True)
