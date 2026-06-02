"""weaver 数据模型. M14 只用 Skill 那一套; Subagent / Workflow / App 留 stub.

不进 SQLAlchemy — M14 文件 + index.json 是单一 source of truth (设计文档 §7.5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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


# ─── App Generation (Krow-inspired, PentaLoom-native) ────────────────────────
# Krow 值得借鉴的是组件化 app 生成模型, 不是 .krow 命名. PentaLoom 用
# app.json 描述“怎么运行”, manifest.json 描述“agent 怎么调用”.


AppType = Literal["pentaloom_app", "html"]
RestartPolicy = Literal["always", "on_failure", "never"]


class AppServiceSpec(BaseModel):
    """Long-running service component in app.json."""

    name: str
    command: list[str]
    port: int | None = None
    workdir: str | None = None
    restart: RestartPolicy = "on_failure"
    python_deps: list[str] | None = None

    @field_validator("command")
    @classmethod
    def _command_non_empty(cls, v: list[str]) -> list[str]:
        if not v or not any(str(x).strip() for x in v):
            raise ValueError("command 不能为空 (至少一个非空 argv)")
        return v


class AppWindowSpec(BaseModel):
    """Desktop window component. entry is relative to files/."""

    name: str
    entry: str
    title: str | None = None
    width: int | None = None
    height: int | None = None


class AppScheduleSpec(BaseModel):
    """Scheduled task component. Public concept is schedule, not cron."""

    name: str
    command: list[str]
    schedule: str
    workdir: str | None = None
    python_deps: list[str] | None = None

    @field_validator("command")
    @classmethod
    def _command_non_empty(cls, v: list[str]) -> list[str]:
        if not v or not any(str(x).strip() for x in v):
            raise ValueError("command 不能为空 (至少一个非空 argv)")
        return v


class AppScriptParam(BaseModel):
    """Parameter definition for a manually triggered script."""

    name: str
    label: str | None = None
    type: Literal["text", "number", "select", "textarea", "toggle", "file"] = "text"
    default: str | None = None
    options: list[str] | None = None


class AppScriptSpec(BaseModel):
    """Manually-triggered one-shot script component."""

    name: str
    command: list[str]
    params: list[AppScriptParam] = Field(default_factory=list)
    workdir: str | None = None
    python_deps: list[str] | None = None

    @field_validator("command")
    @classmethod
    def _command_non_empty(cls, v: list[str]) -> list[str]:
        if not v or not any(str(x).strip() for x in v):
            raise ValueError("command 不能为空 (至少一个非空 argv)")
        return v


class AppWatchSpec(BaseModel):
    """Directory exposed in the app UI for browsing outputs."""

    name: str
    path: str


class AppComponents(BaseModel):
    services: list[AppServiceSpec] = Field(default_factory=list)
    windows: list[AppWindowSpec] = Field(default_factory=list)
    scripts: list[AppScriptSpec] = Field(default_factory=list)
    schedules: list[AppScheduleSpec] = Field(default_factory=list)
    watches: list[AppWatchSpec] = Field(default_factory=list)


class AppDefinition(BaseModel):
    """weaver/apps/<slug>/app.json — PentaLoom runtime declaration.

    This is the "how to run it" layer. InvocableAppManifest remains the
    "how an agent calls it" layer.
    """

    name: str
    version: str = "0.1.0"
    components: AppComponents = Field(default_factory=AppComponents)


class InvocationTarget(BaseModel):
    """Where invoke_app should route an invocation."""

    component: Literal["window", "service", "script"]
    name: str
    handler: str | None = None
    method: Literal["GET", "POST"] | None = None
    path: str | None = None


class InvocationExample(BaseModel):
    name: str
    input: dict[str, Any]
    expected_shape: dict[str, Any] | None = None


class InvocationSpec(BaseModel):
    """单个 invocation 接口声明. input/output schema 走 JSON Schema draft-07
    (后端 Pydantic 校验, renderer 端 AJV 校验, 两端同源)."""

    id: str
    description: str
    target: InvocationTarget | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = 30000
    examples: list[InvocationExample] = Field(default_factory=list)


class ManifestPermissions(BaseModel):
    """app 声明的额外权限白名单. 默认全 deny — 想用 CDN / 想 fetch 外部 host
    必须 manifest 显式声明, 加载 app 时给用户审批."""

    network_hosts: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)


class InvocableAppManifest(BaseModel):
    """weaver/apps/<slug>/manifest.json 全文.

    type='pentaloom_app' 表示按 PentaLoom app.json 组件模型生成/运行;
    type='html' 保留给 App-0/App-Manifest-Loop spike 的单 HTML demo.
    invocations 是核心 — agent 看清单决定哪些 app 能用.
    """

    name: str
    type: AppType = "pentaloom_app"
    description: str
    version: str = "0.1.0"
    invocations: list[InvocationSpec] = Field(default_factory=list)
    permissions: ManifestPermissions = Field(default_factory=ManifestPermissions)


class InvocableAppMeta(BaseModel):
    """weaver/apps/<slug>/meta.json — 跟 manifest.json 互补的运行时元数据
    (跟 SkillMeta 同款 — 不在 manifest 里, 是 PentaLoom 维护的).

    递进式 weave 状态机 (拆 atomic weave_app 后 GPT 建议的收口设计):
      draft   → weave_app 刚建骨架, 还在写 files; invoke_app 拒
      ready   → finalize 通过, 校验完整, 可以 invoke_app
      dirty   → ready 后又改了 file, 需重新 finalize (旧 schema + 新代码不一致风险)
      failed  → finalize 校验失败, 可继续 write/edit 修
    """

    name: str
    kind: Literal["app"] = "app"
    description: str
    source: WeaverSource = "agent_woven"
    status: Literal["draft", "ready", "dirty", "failed"] = "draft"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_finalized_at: datetime | None = None
    last_finalize_error: str | None = None  # failed 时存校验失败原因
    last_used_at: datetime | None = None
    use_count: int = 0
    is_trusted: bool = False
