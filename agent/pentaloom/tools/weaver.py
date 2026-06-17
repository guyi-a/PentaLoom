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
from pentaloom.capabilities.weaver import ephemeral_service
from pentaloom.capabilities.weaver import workflow as workflow_biz
from pentaloom.capabilities.weaver import workflow_runtime
from pentaloom.config import Settings

WEAVER_MCP_SERVER_NAME = "pentaloom_weaver"

WEAVE_SKILL_TOOL_NAME = "weave_skill"
WEAVE_APP_TOOL_NAME = "weave_app"
WEAVE_APP_REVISE_TOOL_NAME = "weave_app_revise"
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

# ephemeral service 织造期 toolset (跟 declared launchd service 双轨)
WEAVE_SERVICE_START_TOOL_NAME = "weave_service_start"
WEAVE_SERVICE_STOP_TOOL_NAME = "weave_service_stop"
WEAVE_SERVICE_RESTART_TOOL_NAME = "weave_service_restart"
WEAVE_SERVICE_LOGS_TOOL_NAME = "weave_service_logs"

# window 开关 — agent 主动开窗 / 关窗 (跟 invoke_app 同档免审, 用户主动让 agent 干就默认信任)
OPEN_APP_WINDOW_TOOL_NAME = "open_app_window"
CLOSE_APP_WINDOW_TOOL_NAME = "close_app_window"

WEAVE_SKILL_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_SKILL_TOOL_NAME}"
WEAVE_APP_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_APP_TOOL_NAME}"
WEAVE_APP_REVISE_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_APP_REVISE_TOOL_NAME}"
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
WEAVE_SERVICE_START_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_SERVICE_START_TOOL_NAME}"
WEAVE_SERVICE_STOP_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_SERVICE_STOP_TOOL_NAME}"
WEAVE_SERVICE_RESTART_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_SERVICE_RESTART_TOOL_NAME}"
WEAVE_SERVICE_LOGS_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_SERVICE_LOGS_TOOL_NAME}"
OPEN_APP_WINDOW_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{OPEN_APP_WINDOW_TOOL_NAME}"
CLOSE_APP_WINDOW_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{CLOSE_APP_WINDOW_TOOL_NAME}"

ALL_WEAVER_FULL_NAMES = (
    WEAVE_SKILL_FULL_NAME,
    WEAVE_APP_FULL_NAME,
    WEAVE_APP_REVISE_FULL_NAME,
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
    之后 evict, 用户下条 message 触发 rebuild 拿到新内容.
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
            "织一个 invocable app 骨架 — 这步只建 manifest + app.json + 空 files/ 目录. "
            "硬规则: "
            "(1) 调本工具前必须先 Skill('app-patterns'); 涉及 window/service 时再按需 "
            "Skill('app-window') / Skill('app-service'). "
            "(2) 当 app 含 window/service/schedule/watch 任 2 个以上组件时, "
            "**调本工具前必须先调 todo_write 拆步骤** (是真调 mcp__pentaloom_todos__todo_write "
            "工具, 不是 Bash echo 一行 / 回复里 markdown 列表 — 那些不算, 右栏 Todo 面板看不到). "
            "todo 步骤**顺序不能乱**, 至少这 5 步: "
            "①Load skills → 设计架构 / "
            "②weave_app 建骨架 / "
            "③weave_app_write_file 写每个组件源码 / "
            "④**verify 每个组件**(service 用 weave_service_start, window 用 open_app_window, "
            "script 用 invoke_app) / "
            "⑤weave_app_finalize 收口. "
            "❌ verify 放 finalize 后 / write 跟 finalize 混一步 — 都是错的, "
            "verify 出错时 plist 已经装上了. ✓ write → verify → finalize 三步分明. "
            "(3) description 参数必须跟 manifest_json 里的 \"description\" 字段**字面量一致** "
            "(两处写同一句, 复制粘贴防错). "
            "(4) app_json 的 components.schedules[] 每项**必须含 invocation_id** "
            "(引用 manifest.invocations[].id). components.watches[] 同款, watch 不触发 "
            "invocation 时设 invocation_id=None (= browse-only). 漏 invocation_id 是 "
            "weave_app 最常见 schema 错. "
            "**不**写源码. 写源码用 weave_app_write_file 增量写, 写完用 "
            "weave_app_finalize 收口. status 初始为 'draft', invoke_app 拒 draft. "
            "manifest_json 必填 (含至少 1 个 invocation); app_json 必填 (runtime 需要); "
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
        # 给 LLM 看的下一步引导 — 多组件 app 强引导 build-verify-show 循环,
        # 不要一把 write + finalize. tool_result 出现在当前 turn, 比 system prompt
        # 早期段更显眼. 注意 todo 应该在调本工具**之前**就拆好 (description
        # 硬规则), 这里 result 假设 todo 已经存在, 强调"继续推进"而不是"现在拆".
        comp = app_biz.read_app_definition(settings, meta.name)
        n_components = 0
        if comp is not None:
            n_components = (
                len(comp.components.windows)
                + len(comp.components.services)
                + len(comp.components.scripts)
                + len(comp.components.schedules)
                + len(comp.components.watches)
            )
        if n_components >= 2:
            hint = (
                f"已建 app {meta.name!r} 骨架 (status=draft, 含 {n_components} 个组件).\n\n"
                "**继续按你的 todo 列表推进** (如果没拆, 现在 mcp__pentaloom_todos__todo_write 补一份):\n"
                "  1. ✓ weave_app 骨架 (刚做完)\n"
                "  2. weave_app_write_file 写每个组件源码\n"
                "  3. **每个组件分别 verify** — service 用 weave_service_start "
                "起 ephemeral + weave_service_logs 看错; window 用 "
                "open_app_window 看 UI 渲不渲; script 用 invoke_app 跑一次\n"
                "  4. 全部 verify 过再 weave_app_finalize 收口装 launchd plist\n\n"
                "中间 verify 出错就改, 别带病 finalize."
            )
        else:
            hint = (
                f"已建 app {meta.name!r} 骨架 (status=draft). "
                "下一步: weave_app_write_file 写源码 → 写完 verify (service 用 "
                "weave_service_start, window 用 open_app_window, script 用 invoke_app) "
                "→ weave_app_finalize 收口. invoke_app 只允许 status=ready."
            )
        return _ok_text(hint)

    @tool(
        WEAVE_APP_REVISE_TOOL_NAME,
        (
            "改 app 的 manifest.json / app.json / description (整体覆盖). "
            "用途: 织造期发现 schema 错 / port 冲突 / target 写错时, **改 app 配置不用 "
            "delete + 重 weave**. files/ 下源码用 weave_app_write_file / edit_file, "
            "本工具不动 files/. "
            "限制: app 必须存在; status 必须是 draft 或 dirty (ready 的 app 拒). "
            "三个字段任一不传 = 保持原值, 至少传一个. "
            "改 description 时必须连带改 manifest_json (manifest.description 字段也要更新). "
            "参数: name (**必填**, str, app 名字), "
            "description (可选, str), "
            "manifest_json (可选, str, 完整新 manifest.json), "
            "app_json (可选, str, 完整新 app.json). "
            "调用例子 (改端口): "
            "{\"name\":\"diary\",\"app_json\":\"{...完整 app.json, services[].port=9002...}\"}. "
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "manifest_json": {"type": "string"},
                "app_json": {"type": "string"},
            },
            "required": ["name"],
        },
    )
    async def _weave_app_revise(args: dict[str, Any]) -> dict[str, Any]:
        try:
            meta = app_biz.revise_app(
                settings,
                name=str(args.get("name", "")),
                description=(
                    str(args["description"]) if args.get("description") is not None else None
                ),
                manifest_json=(
                    str(args["manifest_json"]) if args.get("manifest_json") is not None else None
                ),
                app_json=(
                    str(args["app_json"]) if args.get("app_json") is not None else None
                ),
            )
        except WeaverError as e:
            return _err(f"weave_app_revise 失败: {e}")
        except Exception as e:
            return _err(f"weave_app_revise 未预期错误 {type(e).__name__}: {e}")
        mark_rebuild()
        return _ok_text(
            f"已改 app {meta.name!r} (status={meta.status}). "
            "改完用 weave_service_start / open_app_window 重 verify, 再 weave_app_finalize."
        )

    @tool(
        WEAVE_APP_WRITE_FILE_TOOL_NAME,
        (
            "往已 weave 的 app 的 files/<rel_path> 写一个文件 (创建或覆盖). "
            "自动放行 (app 已 HITL 通过). "
            "参数 (3 个**全必填**, 缺一个就报 validation error): "
            "app_name (str, app 的名字), "
            "rel_path (str, 相对 files/ 的路径, 如 'services/api/main.py'), "
            "content (str, 文件全文). "
            "调用例子: "
            "{\"app_name\":\"diary\",\"rel_path\":\"services/api/main.py\",\"content\":\"...\"}. "
            "路径必须相对 files/ 根, 不能 ../, "
            "不能覆盖 manifest.json/app.json/meta.json/runs/logs. "
            "若 app 是 ready 状态, 写完会打回 dirty (强制重 finalize)."
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
            "参数 (4 个**全必填**): app_name (str), rel_path (str, 相对 files/), "
            "old_string (str, 原文段), new_string (str, 新文段). "
            "调用例子: "
            "{\"app_name\":\"diary\",\"rel_path\":\"services/api/main.py\","
            "\"old_string\":\"port = 9001\",\"new_string\":\"port = 9002\"}. "
            "old_string 必须在文件里唯一出现 1 次 (找不到 / 多次出现都报错, "
            "agent 给更长 context 重试). 改完 ready app 会打回 dirty."
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
        app_name = str(args.get("app_name", ""))
        # declared launchd 接管前先 stop 同名 ephemeral, 防 .runtime/<svc>.port
        # 被 PentaLoom-持有 subprocess 占着导致 launchd 重启的真 service 拿不到端口.
        try:
            stopped = await ephemeral_service.get_registry().stop_all_for_app(app_name)
            if stopped:
                pass  # log 在 ephemeral 内已经打
        except Exception:
            # ephemeral 清理失败不阻塞 finalize — finalize 关键是 plist 装好
            pass
        try:
            result = app_biz.finalize_app(
                settings, app_name=app_name,
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
            "markdown 让你 (主 agent) 自己接管. 你看到 ToolResult 后, 用 todo_write 把 steps 拆成 "
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
            "软删某产物 (整个目录搬到 weaver/.trash/). 30 天后清理 (暂不实装清理). "
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
        kind = str(args.get("kind", ""))
        name = str(args.get("name", ""))
        # 删 app 前先 stop 同名 ephemeral, 否则 .trash/ 下 .runtime/ 还有
        # 端口文件 + ephemeral subprocess 还在跑.
        if kind == "app":
            try:
                await ephemeral_service.get_registry().stop_all_for_app(name)
            except Exception:
                pass
        try:
            result = meta_tools.delete_weaver(
                settings,
                kind=kind,
                name=name,
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
            "运行某产物. 占位 — 当前调用会抛 NotImplemented. "
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

    # ──────────────────────────────────────────────────────────────────
    # ephemeral service toolset (织造期临时跑 service, 跟 declared
    # launchd 双轨). 写同一份 .runtime/<svc>.port, _invoke_service 不区分.
    # finalize_app 触发 reload_for_app 前自动 stop_all_for_app, declared launchd
    # 接管. 这套工具让 agent 在织 app 中就能验证 service 真起得来 + invoke_app
    # 走一遍, 不用每次都 finalize 装 launchd plist.
    # ──────────────────────────────────────────────────────────────────

    @tool(
        WEAVE_SERVICE_START_TOOL_NAME,
        (
            "织造期临时启动 app 的某个 service (subprocess + 动态端口, "
            "memory-only). PentaLoom 重启全清. 同 (app, service) 已在跑 → "
            "先 stop 再 start (idempotent). 写 .runtime/<svc>.port 让 invoke_app "
            "立刻能调. 适用: 织 service 类 app 时验真起得来 / 看错没. "
            "**finalize 后会被自动 stop, declared launchd 接管** — 这套是 dev-time "
            "工具, 长寿命用 weave_app_finalize. "
            "参数: app_name (必填, str, app 名字), "
            "service_name (必填, str, app.json components.services[].name 里的名字). "
            "返: pid / port / log_path / uptime. ready probe (5s 内 TCP 能连 port) "
            "失败但进程没死 → warning 不抛错, agent 用 weave_service_logs 自查."
        ),
        {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "service_name": {"type": "string"},
            },
            "required": ["app_name", "service_name"],
        },
    )
    async def _weave_service_start(args: dict[str, Any]) -> dict[str, Any]:
        try:
            svc = await ephemeral_service.get_registry().start(
                settings,
                app_name=str(args.get("app_name", "")),
                service_name=str(args.get("service_name", "")),
            )
        except ephemeral_service.EphemeralError as e:
            return _err(f"weave_service_start 失败: {e}")
        except Exception as e:
            return _err(
                f"weave_service_start 未预期错误 {type(e).__name__}: {e}"
            )
        return _ok_json(svc.to_dict())

    @tool(
        WEAVE_SERVICE_STOP_TOOL_NAME,
        (
            "停一个织造期 ephemeral service (SIGTERM 3s + 不退 SIGKILL 2s). "
            "顺手删 .runtime/<svc>.port (declared 接管时不留 stale 端口). "
            "参数: app_name (必填, str), service_name (必填, str). "
            "返 stopped=true/false (服务不在 / 已死返 false, 真停了返 true)."
        ),
        {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "service_name": {"type": "string"},
            },
            "required": ["app_name", "service_name"],
        },
    )
    async def _weave_service_stop(args: dict[str, Any]) -> dict[str, Any]:
        try:
            stopped = await ephemeral_service.get_registry().stop(
                app_name=str(args.get("app_name", "")),
                service_name=str(args.get("service_name", "")),
            )
        except Exception as e:
            return _err(
                f"weave_service_stop 未预期错误 {type(e).__name__}: {e}"
            )
        return _ok_json({"stopped": stopped})

    @tool(
        WEAVE_SERVICE_RESTART_TOOL_NAME,
        (
            "stop + start 一个 ephemeral service. 等价 weave_service_stop + "
            "weave_service_start, 但是原子化 + 一条工具调用. 改完 service 源码 "
            "想重跑用这个. "
            "参数: app_name (必填, str), service_name (必填, str). "
            "返同 weave_service_start."
        ),
        {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "service_name": {"type": "string"},
            },
            "required": ["app_name", "service_name"],
        },
    )
    async def _weave_service_restart(args: dict[str, Any]) -> dict[str, Any]:
        app_name = str(args.get("app_name", ""))
        service_name = str(args.get("service_name", ""))
        try:
            reg = ephemeral_service.get_registry()
            await reg.stop(app_name=app_name, service_name=service_name)
            svc = await reg.start(
                settings,
                app_name=app_name,
                service_name=service_name,
            )
        except ephemeral_service.EphemeralError as e:
            return _err(f"weave_service_restart 失败: {e}")
        except Exception as e:
            return _err(
                f"weave_service_restart 未预期错误 {type(e).__name__}: {e}"
            )
        return _ok_json(svc.to_dict())

    @tool(
        WEAVE_SERVICE_LOGS_TOOL_NAME,
        (
            "读 ephemeral service 的 stdout/stderr log 末尾 n 行 (每行含 "
            "[stdout]/[stderr] 前缀). 进程已死也能读 (落盘在 "
            ".ephemeral-logs/<service>.log). declared service log 走 "
            "tail_weaver_logs(kind='app', mode='service:<svc>') — 别混. "
            "参数: app_name (必填, str), service_name (必填, str), "
            "n (可选, int, 末尾多少行, 默认 50). "
            "返: lines (list[str]) / alive / pid / port / log_path."
        ),
        {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "service_name": {"type": "string"},
                "n": {
                    "type": "integer",
                    "description": "末尾多少行, 默认 50.",
                },
            },
            "required": ["app_name", "service_name"],
        },
    )
    async def _weave_service_logs(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await ephemeral_service.get_registry().tail_logs(
                app_name=str(args.get("app_name", "")),
                service_name=str(args.get("service_name", "")),
                n=int(args.get("n") or 50),
            )
        except ephemeral_service.EphemeralError as e:
            return _err(f"weave_service_logs 失败: {e}")
        except Exception as e:
            return _err(
                f"weave_service_logs 未预期错误 {type(e).__name__}: {e}"
            )
        return _ok_json(result)

    # ──────────────────────────────────────────────────────────────────
    # window 开关 — agent 主动开 / 关 window
    # window invocation 最常见的两个动作就是 open / close 本身, 给 agent 原生
    # 工具; 用户主动让 agent 做事时 open 不叫突兀, 是交互. 之后再调
    # invoke_app(target=window) 推数据给 JS handler.
    # ──────────────────────────────────────────────────────────────────

    @tool(
        OPEN_APP_WINDOW_TOOL_NAME,
        (
            "开 app 的 window — spawn loomer 子进程渲 webview, 关 PentaLoom 主壳"
            "也仍活. window_name 不传走 components.windows[0] (单窗 app 最常见). "
            "用户说 '打开 xx' / '弹个 xx 窗口' / 或要 invoke_app 推数据但 window "
            "还没开时调. 返 {window_id, pid}. 已经开着的 window 不去重 — 再调一次"
            "就再开一个 (loom registry findByName 后续 invoke 路由到先开的)."
        ),
        {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "window_name": {
                    "type": "string",
                    "description": "可选, 不传走 components.windows[0]",
                },
            },
            "required": ["app_name"],
        },
    )
    async def _open_app_window(args: dict[str, Any]) -> dict[str, Any]:
        app_name = str(args.get("app_name", "")).strip()
        window_name_raw = args.get("window_name")
        window_name = (
            str(window_name_raw).strip() if window_name_raw else None
        ) or None
        try:
            result = await app_biz.open_window_for_app(
                settings, app_name, window_name=window_name,
            )
        except WeaverError as e:
            return _err(f"open_app_window 失败: {e}")
        except Exception as e:
            return _err(
                f"open_app_window 未预期错误 {type(e).__name__}: {e}"
            )
        return _ok_json(result)

    @tool(
        CLOSE_APP_WINDOW_TOOL_NAME,
        (
            "关 app 的 window — kill loomer 子进程. window_name 不传走 "
            "components.windows[0]. 用户说 '关掉 xx' / '不需要了' 时调. 返 "
            "{closed: bool, window_name}; 窗本来就没开返 closed=false (不是错). "
            "**注意**: 多窗同名时全杀 (重复 open 同名是异常 case)."
        ),
        {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "window_name": {
                    "type": "string",
                    "description": "可选, 不传走 components.windows[0]",
                },
            },
            "required": ["app_name"],
        },
    )
    async def _close_app_window(args: dict[str, Any]) -> dict[str, Any]:
        app_name = str(args.get("app_name", "")).strip()
        window_name_raw = args.get("window_name")
        window_name = (
            str(window_name_raw).strip() if window_name_raw else None
        ) or None
        try:
            result = await app_biz.close_window_for_app(
                settings, app_name, window_name=window_name,
            )
        except WeaverError as e:
            return _err(f"close_app_window 失败: {e}")
        except Exception as e:
            return _err(
                f"close_app_window 未预期错误 {type(e).__name__}: {e}"
            )
        return _ok_json(result)

    return create_sdk_mcp_server(
        name=WEAVER_MCP_SERVER_NAME,
        tools=[
            _weave_skill,
            _weave_app,
            _weave_app_revise,
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
            _weave_service_start,
            _weave_service_stop,
            _weave_service_restart,
            _weave_service_logs,
            _open_app_window,
            _close_app_window,
        ],
    )
