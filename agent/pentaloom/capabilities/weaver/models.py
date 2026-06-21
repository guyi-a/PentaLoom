"""weaver 数据模型.

weaver 产物以文件和 index.json 为单一事实源, 不进 SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

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


# ─── App Generation ─────────────────────────────────────────────────────────
# app.json 描述“怎么运行”, manifest.json 描述“agent 怎么调用”.


AppType = Literal["pentaloom_app", "html"]
RestartPolicy = Literal["always", "on_failure", "never"]


def _validate_python_deps(v: list[str] | None) -> list[str] | None:
    """python_deps 字段共享校验 (service / script / schedule 都用).

    规则:
      - None / [] 合法 (不声明依赖)
      - 元素必须 strip 后非空字符串
      - 不含换行 / NUL 字节 (防 uv add 命令注入)
      - 长度 ≤ 200 char (fastapi[standard]>=0.100.0,<1.0 都够; 太长的 spec 形态可疑)
      - 不限制版本表达式 (uv 自己 parse PEP 508; 错的它会报)
      - 不去自动过滤 stdlib (sqlite3 等); 用户写错 uv add 会失败, 错文案够清晰
    """
    if v is None:
        return None
    out: list[str] = []
    for raw in v:
        if not isinstance(raw, str):
            raise ValueError(f"python_deps 元素必须是 str, 收到 {type(raw).__name__}")
        s = raw.strip()
        if not s:
            raise ValueError("python_deps 元素不能是空串 / 全空白")
        if "\n" in s or "\r" in s or "\x00" in s:
            raise ValueError(
                f"python_deps 元素含换行 / NUL 字节 (防命令注入): {raw!r}"
            )
        if len(s) > 200:
            raise ValueError(
                f"python_deps 元素过长 (>200 char), 检查格式: {s[:50]!r}..."
            )
        out.append(s)
    return out


class AppServiceSpec(BaseModel):
    """Long-running service component in app.json."""

    name: str
    command: list[str]
    port: int | None = None  # None = runtime 动态分配; int = 写死端口 (冲突时 spawn 失败)
    workdir: str | None = None
    restart: RestartPolicy = "on_failure"
    python_deps: list[str] | None = None
    # spawn 后等多少 ms TCP probe 端口可连. 长启动 service 调大.
    startup_timeout_ms: int = 5000

    @field_validator("command")
    @classmethod
    def _command_non_empty(cls, v: list[str]) -> list[str]:
        if not v or not any(str(x).strip() for x in v):
            raise ValueError("command 不能为空 (至少一个非空 argv)")
        return v

    @field_validator("python_deps")
    @classmethod
    def _check_python_deps(cls, v: list[str] | None) -> list[str] | None:
        return _validate_python_deps(v)


class AppWindowSpec(BaseModel):
    """Desktop window component. entry is relative to files/.

    floating widget 4 字段 (titlebar / transparent / always_on_top / movable)
    用于织挂件 (krow 那种 815.15 元/今日 浮动小卡片). 默认值兼容老 app — 普通
    window 跟 macOS 应用外观一致 (titlebar=normal + 系统圆点在).
    """

    name: str
    entry: str
    title: str | None = None
    width: int | None = None
    height: int | None = None
    titlebar: Literal["normal", "hidden"] = "normal"
    """ 'normal' 默认: 标准 macOS 窗带 titlebar + 系统圆点; 'hidden' 挂件: 整个
    titlebar 没了 (含圆点), 内容延伸到顶部. agent 自画 close 按钮调 window.ipc."""
    transparent: bool = False
    """窗 + WKWebView 都不画背景. body { background: transparent } 才能透出去.
    实际形状由 TSX 卡片 div 的圆角 / 阴影决定. 一般跟 titlebar=hidden 一起用."""
    always_on_top: bool = False
    """NSFloatingWindowLevel — 普通 app 切前台不会压住挂件. 监控 / 提醒类专用."""
    movable: bool | None = None
    """任意背景区域可拖. None = 跟 titlebar 联动 (hidden→true, normal→false —
    normal 已经 titlebar 区域可拖, 不需要全屏可拖)."""


class AppScheduleSpec(BaseModel):
    """Scheduled trigger — cron 触发 invocation.

    设计:
      - invocation_id 引用 manifest.invocations[].id, 复用 _invoke_script/window/service
        runtime, 不另起 spawn 路径. finalize 校 invocation_id 存在.
      - schedule 走标准 5-field cron (分 时 日 月 周), 本地时区. croniter 校合法性.
      - args 是固定参数 (跟 invocation input_schema 对齐). watch 触发会在此基础上
        merge events 上下文, schedule 没有触发上下文, 纯固定参数.

    并发: ScheduleTrigger 内部 in-flight bool, overlap 时 skip + 写 runs.jsonl skipped.
    最短粒度 1 分钟 (cron 限制). sub-minute 留远期 (`interval: "10s"` 字段).
    """

    name: str
    schedule: str
    invocation_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    # 声明触发的 invocation 需要的 Python 第三方依赖. Schedule 自身不 spawn 脚本
    # (走 invocation_id 间接触发对应 script/service), 这里声明的 deps 会跟 service /
    # script 的 python_deps 一起收集 + 去重 + 装到 app workspace `.venv`. 也可以
    # 不声明, 让 deps 跟着实际的 script/service component 写. 标准库 (sqlite3 / json)
    # 不要列.
    python_deps: list[str] | None = None

    @field_validator("schedule")
    @classmethod
    def _cron_valid(cls, v: str) -> str:
        from croniter import croniter
        v = v.strip()
        if not croniter.is_valid(v):
            raise ValueError(f"cron 表达式不合法 (5-field): {v!r}")
        return v

    @field_validator("python_deps")
    @classmethod
    def _check_python_deps(cls, v: list[str] | None) -> list[str] | None:
        return _validate_python_deps(v)


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

    @field_validator("python_deps")
    @classmethod
    def _check_python_deps(cls, v: list[str] | None) -> list[str] | None:
        return _validate_python_deps(v)


WatchEvent = Literal["modify", "create", "delete", "move"]


class AppWatchSpec(BaseModel):
    """Watched directory — modal browse + optional invocation trigger.

    path 用于 UI 浏览. 新增字段都可选, 向后兼容: 老 app.json 只有 {name, path}
    时 invocation_id=None → 仅浏览不触发.

    设计:
      - invocation_id=None: 仅 UI 浏览模式, 不起 fs watcher
      - invocation_id=str: 文件事件触发 invocation, watcher 注册, debounce 合并 burst
      - events 默认 modify+create — 一般场景 (产物文件刷新). 不监 access (噪音大)
      - debounce_ms 300ms 默认: vscode / nodemon 同款值, 一次保存的 burst 合并成 1 次触发
      - 触发 args = spec.args ∪ {"events": [{type, path}, ...]} (truncated cap 100)
      - 默认非递归 (recursive=False), 大目录 1000+ 文件递归会拖 Observer

    并发: WatchTrigger 内部 in-flight bool, 期间新事件累计但不触发 (写 skipped log).
    """

    name: str
    path: str
    events: list[WatchEvent] = Field(default_factory=lambda: ["modify", "create"])
    invocation_id: str | None = None
    debounce_ms: int = 300
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("events")
    @classmethod
    def _events_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("events 不能为空 (至少一个: modify/create/delete/move)")
        return v

    @field_validator("debounce_ms")
    @classmethod
    def _debounce_sane(cls, v: int) -> int:
        if v < 0 or v > 60_000:
            raise ValueError(f"debounce_ms 应在 [0, 60000] (毫秒), 收到 {v}")
        return v


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
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] | None = None
    path: str | None = None

    @field_validator("path")
    @classmethod
    def _path_safe(cls, v: str | None) -> str | None:
        """target=service 的 path 必须是相对 HTTP path, 防 SSRF / 越权.

        - 必须 `/` 开头
        - 不能含 `://` (防完整 URL 注入)
        - 不能含 `?` / `#` (query/fragment 走 args, 不在 path 里)
        - 长度限制 1024 防 DoS
        """
        if v is None:
            return None
        if not v.startswith("/"):
            raise ValueError(f"target.path 必须 / 开头 (相对 HTTP path): {v!r}")
        if v.startswith("//"):
            # `//host/x` 是 protocol-relative URL, 会被 httpx 解析成新 host. 拒.
            raise ValueError(f"target.path 不能 // 开头 (protocol-relative URL): {v!r}")
        if "://" in v:
            raise ValueError(f"target.path 不能是完整 URL: {v!r}")
        if "?" in v or "#" in v:
            raise ValueError(f"target.path 不能含 query/fragment (走 args): {v!r}")
        if len(v) > 1024:
            raise ValueError(f"target.path 太长 (>1024)")
        return v


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

    递进式 weave 状态机:
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


# ─── Workflow ───────────────────────────────────────────────────────────────
# workflow = 沉淀流程编排 (skill 沉淀方法论, app 沉淀工具, workflow 把工具+LLM
# 串起来). 5 类设计文档原 5 种 step kind, MVP 先做 3 种线性: invoke_app /
# call_llm / set_var. 不含 if/loop, 不支持 cron 触发 (远期).

WorkflowStepKind = Literal["invoke_app", "call_llm", "set_var"]

# step.id 必须是 [a-z0-9_]+ — workflow 内 mustache 引用 + log 显示都用它做主键.
_STEP_ID_PATTERN = r"^[a-z0-9_]+$"


class _StepBase(BaseModel):
    """3 种 Step 共享的 id 字段 + 校验. discriminator 走 kind."""

    id: str

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        import re
        if not re.match(_STEP_ID_PATTERN, v):
            raise ValueError(
                f"step.id 必须是 ^[a-z0-9_]+$ (mustache 引用主键), 收到 {v!r}"
            )
        return v


class InvokeAppStep(_StepBase):
    """调一个 app 的 invocation. args 里值含 mustache, runtime 渲染."""

    kind: Literal["invoke_app"] = "invoke_app"
    app_name: str
    invocation_id: str
    args: dict[str, Any] = Field(default_factory=dict)


class CallLlmStep(_StepBase):
    """跑一次独立 LLM 调用, 不接 PentaLoom 主对话池.

    output_format=json 时 runtime 强 json.loads; prompt 里建议含 'json' 当 hint
    但不靠它防幻觉.
    """

    kind: Literal["call_llm"] = "call_llm"
    system: str = ""
    prompt: str  # 含 mustache
    output_format: Literal["text", "json"] = "text"
    model: str | None = None  # None → settings.model (注意不是 default_model)
    max_tokens: int = 1024
    timeout_s: int = 60

    @field_validator("prompt")
    @classmethod
    def _prompt_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("call_llm.prompt 不能为空")
        return v


class SetVarStep(_StepBase):
    """给 ctx 设变量. value 是 dict, runtime 递归渲染 mustache (跟 invoke_app.args
    同套规则), 用作'重组 / 重命名'工具 — 把多个 step output 整合成一个新 dict
    给后续 step 当输入."""

    kind: Literal["set_var"] = "set_var"
    value: dict[str, Any] = Field(default_factory=dict)


WorkflowStep = Annotated[
    InvokeAppStep | CallLlmStep | SetVarStep,
    Field(discriminator="kind"),
]


class WorkflowDefinition(BaseModel):
    """workflow.json 主文件 schema. 跟 InvocableAppManifest 平级 (一个描 invocable
    contract, 一个描 step DAG). version 暂只语义化但不强制兼容性."""

    name: str
    description: str
    version: str = "0.1.0"
    inputs_schema: dict[str, Any] = Field(default_factory=dict)
    output_template: dict[str, Any] | None = None
    steps: list[WorkflowStep]

    @field_validator("steps")
    @classmethod
    def _steps_id_unique(cls, v: list[WorkflowStep]) -> list[WorkflowStep]:
        seen: set[str] = set()
        for s in v:
            if s.id in seen:
                raise ValueError(f"step.id 重复: {s.id!r} (workflow 内必须唯一)")
            seen.add(s.id)
        return v


class WorkflowMeta(BaseModel):
    """weaver/workflows/<name>/meta.json. 跟 InvocableAppMeta 平行 — 同款递进式
    weave 状态机 + 时间戳 + 使用计数."""

    name: str
    kind: Literal["workflow"] = "workflow"
    description: str
    source: WeaverSource = "agent_woven"
    status: Literal["draft", "ready", "dirty", "failed"] = "draft"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_finalized_at: datetime | None = None
    last_finalize_error: str | None = None
    last_used_at: datetime | None = None
    use_count: int = 0
