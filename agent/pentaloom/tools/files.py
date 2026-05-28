"""file_read + file_verify 的 in-process MCP 工具.

注册到独立的 server "pentaloom_files" — 跟 workspace / pentaloom_env 解耦, 职责清晰.
完整工具名: mcp__pentaloom_files__{file_read, file_verify}.

HITL 策略:
  - file_read: read-only, 不审批. 进 DEFAULT_ALLOWED_TOOLS.
  - file_verify(autofix=False): read-only, 不审批.
  - file_verify(autofix=True): 改文件, 走 can_use_tool. allow_session key = path,
    同一文件本会话只问一次.

can_use_tool 的"按参数决定要不要审"逻辑放在 tools/workspace.py 的 can_use_tool,
本模块只暴露常量给那边判断.
"""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from pentaloom.capabilities.file._models import FileReadResult, VerifyReport
from pentaloom.capabilities.file._path import resolve_user_path
from pentaloom.capabilities.file.readers.docx import read_docx
from pentaloom.capabilities.file.readers.pptx import read_pptx
from pentaloom.capabilities.file.readers.xlsx import read_xlsx
from pentaloom.capabilities.file.verify.pdf import verify_pdf
from pentaloom.capabilities.file.verify.pptx import verify_pptx

FILES_MCP_SERVER_NAME = "pentaloom_files"
FILE_READ_TOOL_NAME = "file_read"
FILE_VERIFY_TOOL_NAME = "file_verify"

FILE_READ_FULL_NAME = f"mcp__{FILES_MCP_SERVER_NAME}__{FILE_READ_TOOL_NAME}"
FILE_VERIFY_FULL_NAME = f"mcp__{FILES_MCP_SERVER_NAME}__{FILE_VERIFY_TOOL_NAME}"


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _result_to_text(result: FileReadResult | VerifyReport) -> dict[str, Any]:
    """统一把 Pydantic 模型 dump 成 JSON 给 LLM. 不漂亮但稳, agent 解析 JSON 本是基本功."""
    return {
        "content": [
            {
                "type": "text",
                "text": result.model_dump_json(indent=2, exclude_none=True),
            }
        ]
    }


@tool(
    FILE_READ_TOOL_NAME,
    (
        "读取进阶格式: .docx / .pptx / .xlsx, 把文字内容渲成 markdown/csv 给你. "
        "不支持: .pdf / 图片 / .txt / .py / .md / .ipynb 等 — 这些用 Read 工具. "
        "也不支持: .doc / .ppt / .xls (旧二进制) — 让用户先转新格式. "
        "参数: path (绝对路径), sheet (仅 xlsx, 指定 sheet 名, 默认全 sheet), "
        "max_rows (仅 xlsx, 单 sheet 最多读多少行, 默认 200), "
        "max_cols (仅 xlsx, 单 sheet 最多读多少列, 默认 30)."
    ),
    {"path": str, "sheet": str, "max_rows": int, "max_cols": int},
)
async def _file_read(args: dict[str, Any]) -> dict[str, Any]:
    try:
        p = resolve_user_path(str(args.get("path", "")))
    except ValueError as e:
        return _err(str(e))

    suffix = p.suffix.lower()
    try:
        if suffix == ".docx":
            result = read_docx(p)
        elif suffix == ".pptx":
            result = read_pptx(p)
        elif suffix == ".xlsx":
            sheet = args.get("sheet") or None
            max_rows = int(args.get("max_rows") or 200)
            max_cols = int(args.get("max_cols") or 30)
            result = read_xlsx(p, sheet=sheet, max_rows=max_rows, max_cols=max_cols)
        elif suffix in {".doc", ".ppt", ".xls"}:
            return _err(
                f"file_read 不支持旧二进制格式 {suffix}; "
                "让用户用 LibreOffice/Office 另存为 .docx/.pptx/.xlsx 再读."
            )
        else:
            return _err(
                f"file_read 不支持 {suffix}; "
                "纯文本/代码/PDF/图片/notebook 请用 Read 工具."
            )
    except Exception as e:
        return _err(f"读取 {p.name} 失败: {type(e).__name__}: {e}")

    return _result_to_text(result)


@tool(
    FILE_VERIFY_TOOL_NAME,
    (
        "对 .pdf / .pptx 做质量检查 (字体豆腐字 / 几何越界 / 空 slide / 空页). "
        "autofix=True 时尝试自动修复 blocking 项 (PDF 删空页 / PPTX 注入 CJK 字体), "
        "修复会改文件需要用户授权 (同一文件本会话内只问一次). "
        "autofix=False 时只报告不改, read-only 免审. "
        "生成或修改完 .pdf / .pptx 后必须调一次确认质量, 直到 blocking_count=0 才算交付. "
        "参数: path (绝对路径, .pdf 或 .pptx), autofix (默认 True)."
    ),
    {"path": str, "autofix": bool},
)
async def _file_verify(args: dict[str, Any]) -> dict[str, Any]:
    try:
        p = resolve_user_path(str(args.get("path", "")))
    except ValueError as e:
        return _err(str(e))

    autofix = bool(args.get("autofix", True))
    suffix = p.suffix.lower()
    try:
        if suffix == ".pdf":
            report = verify_pdf(p, autofix=autofix)
        elif suffix == ".pptx":
            report = verify_pptx(p, autofix=autofix)
        else:
            return _err(
                f"file_verify 只支持 .pdf / .pptx, 收到 {suffix!r}"
            )
    except Exception as e:
        return _err(f"verify {p.name} 失败: {type(e).__name__}: {e}")

    return _result_to_text(report)


FILES_MCP_SERVER = create_sdk_mcp_server(
    name=FILES_MCP_SERVER_NAME,
    tools=[_file_read, _file_verify],
)

# 给 can_use_tool 判 file_verify 要不要审用 — 没数据, 留接口对齐.
FILES_TOOLS = (_file_read, _file_verify)
