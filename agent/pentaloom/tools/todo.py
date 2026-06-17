"""todo_{write,update,read,delete} — per-session todo 跟踪 MCP 工具.

落库 (session_todos 表) 跨重启保留. per-session: build_todos_mcp_server(session_id=sid)
闭包持 sid, 每个 sid 一个 server 实例.

server 名: pentaloom_todos.
4 个工具 (LLM 看到的全名):
  - mcp__pentaloom_todos__todo_write  — 整体覆盖
  - mcp__pentaloom_todos__todo_update — 改第 seq 项 (status/content/activeForm)
  - mcp__pentaloom_todos__todo_read   — 读当前列表
  - mcp__pentaloom_todos__todo_delete — seq=None 清空, seq=N 删某条后重排

HITL 全部不审批 (self-tracking, 无风险).
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from pentaloom.crud import todos as todos_crud
from pentaloom.infra.db import AsyncSessionLocal

TODOS_MCP_SERVER_NAME = "pentaloom_todos"
TODO_WRITE_TOOL_NAME = "todo_write"
TODO_UPDATE_TOOL_NAME = "todo_update"
TODO_READ_TOOL_NAME = "todo_read"
TODO_DELETE_TOOL_NAME = "todo_delete"

TODO_WRITE_FULL_NAME = f"mcp__{TODOS_MCP_SERVER_NAME}__{TODO_WRITE_TOOL_NAME}"
TODO_UPDATE_FULL_NAME = f"mcp__{TODOS_MCP_SERVER_NAME}__{TODO_UPDATE_TOOL_NAME}"
TODO_READ_FULL_NAME = f"mcp__{TODOS_MCP_SERVER_NAME}__{TODO_READ_TOOL_NAME}"
TODO_DELETE_FULL_NAME = f"mcp__{TODOS_MCP_SERVER_NAME}__{TODO_DELETE_TOOL_NAME}"


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _ok(items: list[dict[str, Any]], note: str | None = None) -> dict[str, Any]:
    """统一返当前列表的 JSON, agent 每次都能看到完整 state."""
    payload: dict[str, Any] = {"todos": items, "count": len(items)}
    if note is not None:
        payload["note"] = note
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ]
    }


def build_todos_mcp_server(*, session_id: str) -> Any:
    """per-session todo 工具集. 闭包持 sid, 工具调用全部按这个 sid 操作 DB."""

    @tool(
        TODO_WRITE_TOOL_NAME,
        (
            "整体覆盖 todo 列表 (overwrite 语义). 用于: 初次拆 todo / 大改重写. "
            "返回当前列表 JSON. "
            "参数: todos (必填, list, 每项含 content / activeForm; status 可选, "
            "默认 pending, 可选 pending|in_progress|completed). "
            "seq 字段不用传, 工具自动按列表顺序赋 1..N."
        ),
        {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "activeForm": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["content", "activeForm"],
                    },
                },
            },
            "required": ["todos"],
        },
    )
    async def _todo_write(args: dict[str, Any]) -> dict[str, Any]:
        raw = args.get("todos")
        if not isinstance(raw, list):
            return _err("todos 必须是 list")
        try:
            async with AsyncSessionLocal() as db:
                items = await todos_crud.write_todos(
                    db, session_id=session_id, todos=raw
                )
        except ValueError as e:
            return _err(f"todo_write 失败: {e}")
        except Exception as e:
            return _err(f"todo_write 未预期错误 {type(e).__name__}: {e}")
        return _ok(items, note=f"已写入 {len(items)} 条 todo")

    @tool(
        TODO_UPDATE_TOOL_NAME,
        (
            "改第 seq 项的 status / content / activeForm. 三个字段任一不传保持原值. "
            "用于: 推进任务 (status pending→in_progress→completed) / 改文本. "
            "返回更新后完整列表. "
            "参数: seq (必填, int, 1-based), "
            "status (可选, pending|in_progress|completed), "
            "content (可选, str), activeForm (可选, str)."
        ),
        {
            "type": "object",
            "properties": {
                "seq": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed"],
                },
                "content": {"type": "string"},
                "activeForm": {"type": "string"},
            },
            "required": ["seq"],
        },
    )
    async def _todo_update(args: dict[str, Any]) -> dict[str, Any]:
        seq = args.get("seq")
        if not isinstance(seq, int):
            return _err("seq 必须是 int")
        status = args.get("status")
        content = args.get("content")
        active_form = args.get("activeForm")
        try:
            async with AsyncSessionLocal() as db:
                items = await todos_crud.update_todo(
                    db,
                    session_id=session_id,
                    seq=seq,
                    status=status if isinstance(status, str) else None,
                    content=content if isinstance(content, str) else None,
                    active_form=active_form if isinstance(active_form, str) else None,
                )
        except ValueError as e:
            return _err(f"todo_update 失败: {e}")
        except Exception as e:
            return _err(f"todo_update 未预期错误 {type(e).__name__}: {e}")
        return _ok(items, note=f"已更新 seq={seq}")

    @tool(
        TODO_READ_TOOL_NAME,
        (
            "读当前 todo 列表 (JSON). 用于: agent 自查自己规划过什么. 无参数."
        ),
        {"type": "object", "properties": {}},
    )
    async def _todo_read(_args: dict[str, Any]) -> dict[str, Any]:
        try:
            async with AsyncSessionLocal() as db:
                items = await todos_crud.read_todos(db, session_id=session_id)
        except Exception as e:
            return _err(f"todo_read 未预期错误 {type(e).__name__}: {e}")
        return _ok(items)

    @tool(
        TODO_DELETE_TOOL_NAME,
        (
            "删 todo. 不传 seq = 清空所有; 传 seq = 删第 seq 项, 后续 seq 重排. "
            "返回删除后完整列表. "
            "参数: seq (可选, int, 1-based)."
        ),
        {
            "type": "object",
            "properties": {
                "seq": {"type": "integer"},
            },
        },
    )
    async def _todo_delete(args: dict[str, Any]) -> dict[str, Any]:
        seq = args.get("seq")
        if seq is not None and not isinstance(seq, int):
            return _err("seq 必须是 int 或不传")
        try:
            async with AsyncSessionLocal() as db:
                items = await todos_crud.delete_todo(
                    db, session_id=session_id, seq=seq
                )
        except ValueError as e:
            return _err(f"todo_delete 失败: {e}")
        except Exception as e:
            return _err(f"todo_delete 未预期错误 {type(e).__name__}: {e}")
        note = "已清空" if seq is None else f"已删 seq={seq}"
        return _ok(items, note=note)

    return create_sdk_mcp_server(
        name=TODOS_MCP_SERVER_NAME,
        tools=[_todo_write, _todo_update, _todo_read, _todo_delete],
    )
