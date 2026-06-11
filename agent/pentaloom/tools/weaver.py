"""weaver in-process MCP 工具 — weave_skill / weave_app + 6 个 meta-tool.

跟 search.py / browser.py 同款 @tool decorator + create_sdk_mcp_server. 区别:
**per-session 构造** (不是 module-level singleton), 因为 weave_skill / edit_weaver /
delete_weaver 完成时要回调 LoomPool.mark_pending_rebuild(sid) — 闭包持 sid + callback,
跟 build_browser_mcp_server 模式一致.

description 分两层:
  - @tool description (LLM 工具清单): 工具说明书 (参数 / 返什么 / 约束)
  - 决策引导 (何时 weave / 何时 manage): prompts/weaver.py WEAVER_PROMPT_INSTRUCTIONS

HITL:
  - weave_skill / edit_weaver / delete_weaver / run_weaver 弹审 (workspace.py HITL_TOOL_NAMES)
  - list_weaver / inspect_weaver / tail_weaver_logs 免审 (只读)
"""

from __future__ import annotations

import json
from typing import Any, Callable

from claude_agent_sdk import create_sdk_mcp_server, tool

from pentaloom.capabilities.weaver import WeaverError, app as app_biz, meta_tools, skill
from pentaloom.capabilities.weaver import app_runtime
from pentaloom.capabilities.weaver import workflow as workflow_biz
from pentaloom.capabilities.weaver import workflow_runtime
from pentaloom.config import Settings

WEAVER_MCP_SERVER_NAME = "pentaloom_weaver"

WEAVE_SKILL_TOOL_NAME = "weave_skill"
WEAVE_APP_TOOL_NAME = "weave_app"
WEAVE_APP_WRITE_FILE_TOOL_NAME = "weave_app_write_file"
WEAVE_APP_EDIT_FILE_TOOL_NAME = "weave_app_edit_file"
WEAVE_APP_FINALIZE_TOOL_NAME = "weave_app_finalize"
INVOKE_APP_TOOL_NAME = "invoke_app"
WEAVE_WORKFLOW_TOOL_NAME = "weave_workflow"
WEAVE_WORKFLOW_FINALIZE_TOOL_NAME = "weave_workflow_finalize"
INVOKE_WORKFLOW_TOOL_NAME = "invoke_workflow"
INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME = "invoke_workflow_dynamic"
LIST_WEAVER_TOOL_NAME = "list_weaver"
INSPECT_WEAVER_TOOL_NAME = "inspect_weaver"
EDIT_WEAVER_TOOL_NAME = "edit_weaver"
DELETE_WEAVER_TOOL_NAME = "delete_weaver"
RUN_WEAVER_TOOL_NAME = "run_weaver"
TAIL_WEAVER_LOGS_TOOL_NAME = "tail_weaver_logs"

WEAVE_SKILL_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_SKILL_TOOL_NAME}"
WEAVE_APP_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_APP_TOOL_NAME}"
WEAVE_APP_WRITE_FILE_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_APP_WRITE_FILE_TOOL_NAME}"
WEAVE_APP_EDIT_FILE_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_APP_EDIT_FILE_TOOL_NAME}"
WEAVE_APP_FINALIZE_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_APP_FINALIZE_TOOL_NAME}"
INVOKE_APP_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{INVOKE_APP_TOOL_NAME}"
WEAVE_WORKFLOW_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_WORKFLOW_TOOL_NAME}"
WEAVE_WORKFLOW_FINALIZE_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_WORKFLOW_FINALIZE_TOOL_NAME}"
INVOKE_WORKFLOW_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{INVOKE_WORKFLOW_TOOL_NAME}"
INVOKE_WORKFLOW_DYNAMIC_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME}"
LIST_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{LIST_WEAVER_TOOL_NAME}"
INSPECT_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{INSPECT_WEAVER_TOOL_NAME}"
EDIT_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{EDIT_WEAVER_TOOL_NAME}"
DELETE_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{DELETE_WEAVER_TOOL_NAME}"
RUN_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{RUN_WEAVER_TOOL_NAME}"
TAIL_WEAVER_LOGS_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{TAIL_WEAVER_LOGS_TOOL_NAME}"

ALL_WEAVER_FULL_NAMES = (
    WEAVE_SKILL_FULL_NAME,
    WEAVE_APP_FULL_NAME,
    WEAVE_APP_WRITE_FILE_FULL_NAME,
    WEAVE_APP_EDIT_FILE_FULL_NAME,
    WEAVE_APP_FINALIZE_FULL_NAME,
    INVOKE_APP_FULL_NAME,
    WEAVE_WORKFLOW_FULL_NAME,
    WEAVE_WORKFLOW_FINALIZE_FULL_NAME,
    INVOKE_WORKFLOW_FULL_NAME,
    INVOKE_WORKFLOW_DYNAMIC_FULL_NAME,
    LIST_WEAVER_FULL_NAME,
    INSPECT_WEAVER_FULL_NAME,
    EDIT_WEAVER_FULL_NAME,
    DELETE_WEAVER_FULL_NAME,
    RUN_WEAVER_FULL_NAME,
    TAIL_WEAVER_LOGS_FULL_NAME,
)


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _ok_json(payload: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            }
        ]
    }


def _ok_text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def build_weaver_mcp_server(
    settings: Settings, mark_rebuild: Callable[[], None]
) -> Any:
    """per-session 构造 weaver MCP server. mark_rebuild 是 LoomPool 注入的回调.

    weave / edit / delete 成功时 mark_rebuild() — LoomPool 在当前 turn stream_end
    之后 evict, 用户下条 message 触发 rebuild 拿到新内容 (Spike 1+2+3 verified).
    """

    @tool(
        WEAVE_SKILL_TOOL_NAME,
        (
            "沉淀一个 Skill (markdown 写给未来 agent 看的 SOP). "
            "成功后 mark pending rebuild, 当前 turn 不可用, 用户下条 message 起 agent 自动加载. "
            "参数: "
            "name (必填, kebab-case, 必须跟 frontmatter.name 一致, ≤64 chars); "
            "description (必填, 一句话描述, 必须跟 frontmatter.description 一致); "
            "content (必填, 完整 SKILL.md 含 YAML frontmatter — 至少 name + description, 推荐加 when_to_use)."
        ),
        {"name": str, "description": str, "content": str},
    )
    async def _weave_skill(args: dict[str, Any]) -> dict[str, Any]:
        try:
            meta = skill.weave_skill(
                settings,
                name=str(args.get("name", "")),
                description=str(args.get("description", "")),
                content=str(args.get("content", "")),
            )
        except WeaverError as e:
            return _err(f"weave_skill 失败: {e}")
        except Exception as e:
            return _err(f"weave_skill 未预期错误 {type(e).__name__}: {e}")
        mark_rebuild()
        return _ok_text(
            f"已沉淀 skill {meta.name!r}. 当前对话不可见; 你下条 message 起 agent 自动加载."
        )

    @tool(
        WEAVE_APP_TOOL_NAME,
        (
            "织一个 invocable app 骨架 — 这步只建 manifest + app.json + 空 files/ 目录, "
            "**不**写源码. 写源码用 weave_app_write_file 增量写, 写完用 "
            "weave_app_finalize 收口. status 初始为 'draft', invoke_app 拒 draft. "
            "manifest_json 必填 (含至少 1 个 invocation); app_json 必填 (Phase B+ runtime 需要); "
            "files 可选 (向后兼容老 caller, 给了也只是写盘, status 仍 draft)."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "manifest_json": {"type": "string"},
                "app_json": {"type": "string"},
                "files": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "可选, 向后兼容. 推荐留空, 用 weave_app_write_file 递增写.",
                },
            },
            "required": ["name", "description", "manifest_json"],
        },
    )
    async def _weave_app(args: dict[str, Any]) -> dict[str, Any]:
        try:
            files = args.get("files") or {}
            if not isinstance(files, dict):
                return _err("files 必须是 {relative_path: content} dict")
            meta = app_biz.weave_app(
                settings,
                name=str(args.get("name", "")),
                description=str(args.get("description", "")),
                manifest_json=str(args.get("manifest_json", "")),
                files=files,
                app_json=(
                    str(args["app_json"])
                    if args.get("app_json") is not None else None
                ),
            )
        except WeaverError as e:
            return _err(f"weave_app 失败: {e}")
        except Exception as e:
            return _err(f"weave_app 未预期错误 {type(e).__name__}: {e}")
        mark_rebuild()
        return _ok_text(
            f"已建 app {meta.name!r} 骨架 (status=draft). "
            "下一步: 用 weave_app_write_file 写源码, 写完用 weave_app_finalize 收口. "
            "invoke_app 只允许 status=ready."
        )

    @tool(
        WEAVE_APP_WRITE_FILE_TOOL_NAME,
        (
            "往已 weave 的 app 的 files/<rel_path> 写一个文件 (创建或覆盖). "
            "自动放行 (app 已 HITL 通过). 路径必须相对 files/ 根, 不能 ../, "
            "不能覆盖 manifest.json/app.json/meta.json/runs/logs. "
            "若 app 是 ready 状态, 写完会打回 dirty (强制重 finalize). "
            "参数: app_name (必填), rel_path (必填, 相对 files/), content (必填, 文件全文)."
        ),
        {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "rel_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["app_name", "rel_path", "content"],
        },
    )
    async def _weave_app_write_file(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = app_biz.write_app_file(
                settings,
                app_name=str(args.get("app_name", "")),
                rel_path=str(args.get("rel_path", "")),
                content=str(args.get("content", "")),
            )
        except WeaverError as e:
            return _err(f"weave_app_write_file 失败: {e}")
        except Exception as e:
            return _err(f"weave_app_write_file 未预期错误 {type(e).__name__}: {e}")
        return _ok_json(result)

    @tool(
        WEAVE_APP_EDIT_FILE_TOOL_NAME,
        (
            "改 app 已有文件单段 (跟 SDK Edit 同款语义). 自动放行. "
            "old_string 必须在文件里唯一出现 1 次 (找不到 / 多次出现都报错, "
            "agent 给更长 context 重试). 改完 ready app 会打回 dirty. "
            "参数: app_name, rel_path, old_string, new_string (全必填)."
        ),
        {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "rel_path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["app_name", "rel_path", "old_string", "new_string"],
        },
    )
    async def _weave_app_edit_file(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = app_biz.edit_app_file(
                settings,
                app_name=str(args.get("app_name", "")),
                rel_path=str(args.get("rel_path", "")),
                old_string=str(args.get("old_string", "")),
                new_string=str(args.get("new_string", "")),
            )
        except WeaverError as e:
            return _err(f"weave_app_edit_file 失败: {e}")
        except Exception as e:
            return _err(f"weave_app_edit_file 未预期错误 {type(e).__name__}: {e}")
        return _ok_json(result)

    @tool(
        WEAVE_APP_FINALIZE_TOOL_NAME,
        (
            "递进式 weave 的收口点 — 4 项校验全过 → status=ready (invoke_app 可调). "
            "校验: manifest schema + app.json schema + invocation.target → component 存在 + "
            "每个 script.command 入口文件存在 (e.g., python scripts/h.py 必须有 files/scripts/h.py). "
            "失败: status=failed + 错误信息存 last_finalize_error. 修完重 finalize 即可. "
            "自动放行. 参数: app_name."
        ),
        {
            "type": "object",
            "properties": {"app_name": {"type": "string"}},
            "required": ["app_name"],
        },
    )
    async def _weave_app_finalize(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = app_biz.finalize_app(
                settings, app_name=str(args.get("app_name", "")),
            )
        except WeaverError as e:
            return _err(f"weave_app_finalize 校验失败: {e}")
        except Exception as e:
            return _err(f"weave_app_finalize 未预期错误 {type(e).__name__}: {e}")
        return _ok_json(result)

    @tool(
        INVOKE_APP_TOOL_NAME,
        (
            "调一个已 weave 的 invocable app 的 invocation, 拿 result. "
            "当前只支持 target.component='script' (uv venv subprocess + "
            "stdin JSON + stdout JSON + JSON Schema 双端校验); window / service "
            "在后续 Phase 实装. "
            "参数: name (app 名, 必填); invocation_id (manifest 里的 id, 必填); "
            "args (input_schema 形态的 dict, 可选, 默认 {}). "
            "返 {run_id, status, duration_ms, output} — output 是 handler 实际返的 "
            "(已通过 output_schema 校验); run_id 可拿去 tail_weaver_logs(kind='app') 排查."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "invocation_id": {"type": "string"},
                "args": {"type": "object"},
            },
            "required": ["name", "invocation_id"],
        },
    )
    async def _invoke_app(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await app_runtime.invoke_app(
                settings,
                app_name=str(args.get("name", "")),
                invocation_id=str(args.get("invocation_id", "")),
                args=args.get("args") if isinstance(args.get("args"), dict) else {},
            )
        except app_runtime.InvokeError as e:
            return _err(f"invoke_app 失败: {e}")
        except WeaverError as e:
            return _err(f"invoke_app 失败: {e}")
        except Exception as e:
            return _err(f"invoke_app 未预期错误 {type(e).__name__}: {e}")
        return _ok_json(result)

    @tool(
        WEAVE_WORKFLOW_TOOL_NAME,
        (
            "织一个 dynamic workflow — 流程编排产物, 把多步 invoke_app/call_llm/set_var "
            "串起来. 整 workflow 一个 JSON 主文件 (workflow.json), 没 files/ 子树. "
            "成功后 status='draft', mark pending rebuild, 用户下条 message agent 重载. "
            "必须再调 weave_workflow_finalize 收口才能 invoke_workflow. "
            "参数: "
            "name (必填, kebab-case, 必须跟 definition_json.name 一致, ≤64 chars); "
            "description (必填, 一句话, 必须跟 definition_json.description 一致); "
            "definition_json (必填, 完整 WorkflowDefinition JSON 字符串 — name/description/"
            "version/inputs_schema/steps[]/output_template?). "
            "step kind: invoke_app / call_llm / set_var. "
            "步骤间引用走 mustache: {{inputs.X}} 或 {{steps.<id>.output.<path>}}."
        ),
        {"name": str, "description": str, "definition_json": str},
    )
    async def _weave_workflow(args: dict[str, Any]) -> dict[str, Any]:
        try:
            meta = workflow_biz.weave_workflow(
                settings,
                name=str(args.get("name", "")),
                description=str(args.get("description", "")),
                definition_json=str(args.get("definition_json", "")),
            )
        except WeaverError as e:
            return _err(f"weave_workflow 失败: {e}")
        except Exception as e:
            return _err(f"weave_workflow 未预期错误 {type(e).__name__}: {e}")
        mark_rebuild()
        return _ok_text(
            f"已建 workflow {meta.name!r} (status=draft, {len(workflow_biz.read_workflow(settings, meta.name).steps)} steps). "
            f"现在调 weave_workflow_finalize 校验 → status=ready 就能 invoke_workflow."
        )

    @tool(
        WEAVE_WORKFLOW_FINALIZE_TOOL_NAME,
        (
            "workflow 收口校验 — 通过 → status=ready (invoke_workflow 可调). "
            "校验: schema + step.id 唯一/格式 + mustache 引用合法 (forward-ref / inputs key) + "
            "invoke_app 软校 (app_name 不存在仅 warn, 不挡). "
            "失败: status=failed, 错误信息存 last_finalize_error. 修完重 finalize 即可. "
            "自动放行 (跟 weave_app_finalize 一致). 参数: workflow_name."
        ),
        {
            "type": "object",
            "properties": {"workflow_name": {"type": "string"}},
            "required": ["workflow_name"],
        },
    )
    async def _weave_workflow_finalize(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = workflow_biz.finalize_workflow(
                settings, name=str(args.get("workflow_name", "")),
            )
        except WeaverError as e:
            return _err(f"weave_workflow_finalize 校验失败: {e}")
        except Exception as e:
            return _err(f"weave_workflow_finalize 未预期错误 {type(e).__name__}: {e}")
        return _ok_json(result)

    @tool(
        INVOKE_WORKFLOW_TOOL_NAME,
        (
            "调一个已 finalize 的 workflow, 跑 step DAG 拿 result. "
            "每步 dispatch 到 invoke_app / call_llm / set_var, mustache 渲染步骤间引用, "
            "中间步失败 → 整 workflow 失败 + 后续步不跑 (没 retry). "
            "call_llm 用独立 anthropic client (跟主对话同 endpoint, 但 stateless 不接 LoomPool). "
            "参数: name (workflow 名, 必填); args (按 inputs_schema 形态的 dict, 可选, 默认 {}). "
            "返 {run_id, status, duration_ms, output, steps[], error?} — output 是最终 output_template "
            "渲染结果或最后一步 output; steps 含每步 status/duration/output_summary, invoke_app 步还含 "
            "app_name/invocation_id/app_run_id 给 modal 追踪用."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "args": {"type": "object"},
            },
            "required": ["name"],
        },
    )
    async def _invoke_workflow(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await workflow_runtime.invoke_workflow(
                settings,
                name=str(args.get("name", "")),
                args=args.get("args") if isinstance(args.get("args"), dict) else {},
            )
        except workflow_runtime.WorkflowError as e:
            return _err(f"invoke_workflow 失败: {e}")
        except Exception as e:
            return _err(f"invoke_workflow 未预期错误 {type(e).__name__}: {e}")
        return _ok_json(result)

    @tool(
        INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME,
        (
            "动态模式调 workflow — 不跑 step DAG, 而是把 workflow definition 渲染成一段 plan "
            "markdown 让你 (主 agent) 自己接管. 你看到 ToolResult 后, 用 TodoWrite 把 steps 拆成 "
            "todo, 一步步调 invoke_app / 自己 reasoning / 自己 reply, 跟普通对话一样. "
            "适用: workflow 是给主 agent 看的 plan / SOP, 想用 agent 全套能力执行 (临时改 args, "
            "中途读文件, 把 LLM 处理那步直接想清楚就行). "
            "对比 invoke_workflow (静态版): 静态版 stateless 跑 step DAG 拿 output, 适合 cron / "
            "无人值守; 动态版你接管, 适合主对话里走 plan. "
            "schema 同一份 (用户织一遍, 静态/动态都能调). "
            "call_llm step 在动态版降级为'你自己处理这步'; set_var 降级为'记住这些常量'. "
            "参数: name (workflow 名, 必填); inputs (按 inputs_schema 形态的 dict, 可选). "
            "返一段 plan markdown, 不返结构化 output."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "inputs": {"type": "object"},
            },
            "required": ["name"],
        },
    )
    async def _invoke_workflow_dynamic(args: dict[str, Any]) -> dict[str, Any]:
        try:
            plan_md = await workflow_runtime.render_dynamic_plan(
                settings,
                name=str(args.get("name", "")),
                inputs=args.get("inputs") if isinstance(args.get("inputs"), dict) else {},
            )
        except workflow_runtime.WorkflowError as e:
            return _err(f"invoke_workflow_dynamic 失败: {e}")
        except Exception as e:
            return _err(f"invoke_workflow_dynamic 未预期错误 {type(e).__name__}: {e}")
        return _ok_text(plan_md)

    @tool(
        LIST_WEAVER_TOOL_NAME,
        (
            "列用户织的 weaver 产物. 返 {counts, items}. items 是 [{name, kind, description, source, path}, ...]. "
            "不含 PentaLoom 内置 skill (那是出厂能力, 跟 weaver 体系无关). "
            "kind / query 都可选, 不传就列全部."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "可选, 'skill' | 'subagent' | 'workflow' | 'app'; 不传列全部.",
                },
                "query": {
                    "type": "string",
                    "description": "可选, 在 name + description 里子串搜.",
                },
            },
            "required": [],
        },
    )
    async def _list_weaver(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = meta_tools.list_weaver(
                settings,
                kind=str(args.get("kind") or ""),
                query=str(args.get("query") or ""),
            )
        except WeaverError as e:
            return _err(f"list_weaver 失败: {e}")
        return _ok_json(result)

    @tool(
        INSPECT_WEAVER_TOOL_NAME,
        (
            "读某产物的完整内容. skill 返 {name, kind, source, description, content (SKILL.md 全文), meta}. "
            "支持 kind='skill' 或 kind='app'. "
            "不能 inspect 内置 skill (那是出厂能力, 直接 Read agent/.claude/skills/<name>/SKILL.md)."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "'skill' | 'app'."},
                "name": {"type": "string", "description": "产物名."},
            },
            "required": ["kind", "name"],
        },
    )
    async def _inspect_weaver(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = meta_tools.inspect_weaver(
                settings,
                kind=str(args.get("kind", "")),
                name=str(args.get("name", "")),
            )
        except WeaverError as e:
            return _err(f"inspect_weaver 失败: {e}")
        return _ok_json(result)

    @tool(
        EDIT_WEAVER_TOOL_NAME,
        (
            "改某产物全文. skill 是改 SKILL.md (frontmatter.name 不允许变, 改名 = delete + 新 weave). "
            "成功后 mark rebuild, 用户下条 message 起新内容生效."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "现仅 'skill'."},
                "name": {"type": "string", "description": "产物名."},
                "new_content": {"type": "string", "description": "完整新 SKILL.md (含 YAML frontmatter)."},
            },
            "required": ["kind", "name", "new_content"],
        },
    )
    async def _edit_weaver(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = meta_tools.edit_weaver(
                settings,
                kind=str(args.get("kind", "")),
                name=str(args.get("name", "")),
                new_content=str(args.get("new_content", "")),
            )
        except WeaverError as e:
            return _err(f"edit_weaver 失败: {e}")
        except Exception as e:
            return _err(f"edit_weaver 未预期错误 {type(e).__name__}: {e}")
        mark_rebuild()
        return _ok_json(result)

    @tool(
        DELETE_WEAVER_TOOL_NAME,
        (
            "软删某产物 (整个目录搬到 weaver/.trash/). 30 天后清理 (M14 暂不实装清理). "
            "成功后 mark rebuild, 用户下条 message 起 agent 看不到这个产物."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "'skill' | 'app'."},
                "name": {"type": "string", "description": "产物名."},
            },
            "required": ["kind", "name"],
        },
    )
    async def _delete_weaver(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = meta_tools.delete_weaver(
                settings,
                kind=str(args.get("kind", "")),
                name=str(args.get("name", "")),
            )
        except WeaverError as e:
            return _err(f"delete_weaver 失败: {e}")
        except Exception as e:
            return _err(f"delete_weaver 未预期错误 {type(e).__name__}: {e}")
        mark_rebuild()
        return _ok_json(result)

    @tool(
        RUN_WEAVER_TOOL_NAME,
        (
            "运行某产物. M14 占位 — workflow 实装在 M16, 当前调用会抛 NotImplemented. "
            "skill / subagent 不走这条 (skill 被动加载, subagent 走 Task 派单)."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "name": {"type": "string"},
                "args": {"type": "object", "description": "可选, 传给 workflow 的 inputs."},
            },
            "required": ["kind", "name"],
        },
    )
    async def _run_weaver(args: dict[str, Any]) -> dict[str, Any]:
        kind = str(args.get("kind", ""))
        name = str(args.get("name", ""))
        # workflow 走异步路径 — meta_tools.run_weaver 是 sync, 内部 asyncio.run 在
        # async 工具体里会挂 (running event loop). 直接 await runtime 绕过同步层.
        if kind == "workflow":
            try:
                result = await workflow_runtime.invoke_workflow(
                    settings,
                    name=name,
                    args=args.get("args") if isinstance(args.get("args"), dict) else {},
                )
            except workflow_runtime.WorkflowError as e:
                return _err(f"run_weaver(workflow) 失败: {e}")
            except WeaverError as e:
                return _err(f"run_weaver(workflow) 失败: {e}")
            except Exception as e:
                return _err(f"run_weaver(workflow) 未预期错误 {type(e).__name__}: {e}")
            return _ok_json(result)
        try:
            result = meta_tools.run_weaver(
                settings,
                kind=kind,
                name=name,
                args=args.get("args") or {},
            )
        except WeaverError as e:
            return _err(f"run_weaver 失败: {e}")
        except NotImplementedError as e:
            return _err(str(e))
        return _ok_json(result)

    @tool(
        TAIL_WEAVER_LOGS_TOOL_NAME,
        (
            "读某 app 的运行记录 / service 日志. kind 当前只支持 'app'. "
            "mode='runs' (默认) 返 invoke_app 历次 run jsonl; "
            "mode='service:<svc_name>' 返该 service 的 stdout/stderr log (含 [stdout]/[stderr] 前缀). "
            "想 debug uvicorn / fastapi 输出走 service mode, **不要** Read weaver/ 内 log file."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "'app'."},
                "name": {"type": "string", "description": "app 名."},
                "n": {"type": "integer", "description": "最多返多少条 / 行, 默认 20."},
                "mode": {
                    "type": "string",
                    "description": "'runs' (默认) 或 'service:<svc_name>' (e.g., 'service:api').",
                },
            },
            "required": ["kind", "name"],
        },
    )
    async def _tail_weaver_logs(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = meta_tools.tail_weaver_logs(
                settings,
                kind=str(args.get("kind", "")),
                name=str(args.get("name", "")),
                n=int(args.get("n") or 20),
                mode=str(args.get("mode") or "runs"),
            )
        except WeaverError as e:
            return _err(f"tail_weaver_logs 失败: {e}")
        except NotImplementedError as e:
            return _err(str(e))
        return _ok_json(result)

    return create_sdk_mcp_server(
        name=WEAVER_MCP_SERVER_NAME,
        tools=[
            _weave_skill,
            _weave_app,
            _weave_app_write_file,
            _weave_app_edit_file,
            _weave_app_finalize,
            _invoke_app,
            _weave_workflow,
            _weave_workflow_finalize,
            _invoke_workflow,
            _invoke_workflow_dynamic,
            _list_weaver,
            _inspect_weaver,
            _edit_weaver,
            _delete_weaver,
            _run_weaver,
            _tail_weaver_logs,
        ],
    )
