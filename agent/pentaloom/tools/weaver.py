"""weaver in-process MCP 工具 — 7 个: 1 个 weave_skill + 6 个 meta-tool.

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

from pentaloom.capabilities.weaver import WeaverError, meta_tools, skill
from pentaloom.config import Settings

WEAVER_MCP_SERVER_NAME = "pentaloom_weaver"

WEAVE_SKILL_TOOL_NAME = "weave_skill"
LIST_WEAVER_TOOL_NAME = "list_weaver"
INSPECT_WEAVER_TOOL_NAME = "inspect_weaver"
EDIT_WEAVER_TOOL_NAME = "edit_weaver"
DELETE_WEAVER_TOOL_NAME = "delete_weaver"
RUN_WEAVER_TOOL_NAME = "run_weaver"
TAIL_WEAVER_LOGS_TOOL_NAME = "tail_weaver_logs"

WEAVE_SKILL_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{WEAVE_SKILL_TOOL_NAME}"
LIST_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{LIST_WEAVER_TOOL_NAME}"
INSPECT_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{INSPECT_WEAVER_TOOL_NAME}"
EDIT_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{EDIT_WEAVER_TOOL_NAME}"
DELETE_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{DELETE_WEAVER_TOOL_NAME}"
RUN_WEAVER_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{RUN_WEAVER_TOOL_NAME}"
TAIL_WEAVER_LOGS_FULL_NAME = f"mcp__{WEAVER_MCP_SERVER_NAME}__{TAIL_WEAVER_LOGS_TOOL_NAME}"

ALL_WEAVER_FULL_NAMES = (
    WEAVE_SKILL_FULL_NAME,
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
            "M14 阶段只支持 kind='skill'; 其他 kind 抛错. "
            "不能 inspect 内置 skill (那是出厂能力, 直接 Read agent/.claude/skills/<name>/SKILL.md)."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "现仅 'skill'."},
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
                "kind": {"type": "string", "description": "现仅 'skill'."},
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
        try:
            result = meta_tools.run_weaver(
                settings,
                kind=str(args.get("kind", "")),
                name=str(args.get("name", "")),
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
            "读某产物最近运行记录. M14 占位 — 依赖 workflow_runs (M16 才有), 当前调用抛 NotImplemented."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "name": {"type": "string"},
                "n": {"type": "integer", "description": "最多返多少条, 默认 20."},
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
            _list_weaver,
            _inspect_weaver,
            _edit_weaver,
            _delete_weaver,
            _run_weaver,
            _tail_weaver_logs,
        ],
    )
