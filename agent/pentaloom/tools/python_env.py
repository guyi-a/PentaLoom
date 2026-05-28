"""Python 脚本相关的 in-process MCP 工具.

两个工具 (都过 can_use_tool HITL):
  - install_python_libs(libs, reason): 把 libs 加进共享 uv project. 用户授权后跑 uv add.
    allow_session 的判定 key = sorted(libs) tuple → 同一个组合下次免审.
  - run_python_script(script_path, args, description): 跑沙箱里的 .py 脚本.
    永远 allow_once (脚本内容每次都不一样, 攒白名单没意义).

脚本路径: LLM 先用 Write 把 .py 写到 sandbox_dir_for(sid)/scripts/xxx.py 里,
再把绝对路径传过来. 这里只做 path 白名单校验 (必须在 sandbox 或 mounted_dirs 下).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from pentaloom.config import get_settings
from pentaloom.infra import python_env

PYTHON_ENV_MCP_SERVER_NAME = "pentaloom_env"

INSTALL_LIBS_TOOL_NAME = "install_python_libs"
RUN_SCRIPT_TOOL_NAME = "run_python_script"

INSTALL_LIBS_FULL_NAME = (
    f"mcp__{PYTHON_ENV_MCP_SERVER_NAME}__{INSTALL_LIBS_TOOL_NAME}"
)
RUN_SCRIPT_FULL_NAME = (
    f"mcp__{PYTHON_ENV_MCP_SERVER_NAME}__{RUN_SCRIPT_TOOL_NAME}"
)


# LLM 习惯把整段 Python 直接塞进 script_path. 探测到就回错让它改走 "先 Write
# 文件再传路径" 的路 — 否则 inline 代码 escape 灾难 + 没法 Read 复审 + traceback
# 行号对不上.
_INLINE_CODE_MARKERS = (
    "\n",
    "import ",
    "from ",
    "def ",
    "class ",
    "print(",
    "if __name__",
    "for ",
    "while ",
    "try:",
)


def _looks_like_inline_python(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    # 长 + 含空白 → 多半是被压成一行的代码 (LLM 偶尔这么写)
    if len(s) > 200 and (" " in s or "\t" in s):
        return True
    return any(m in s for m in _INLINE_CODE_MARKERS)


def _format_script_result_text(action: str, result: python_env.ScriptResult) -> str:
    """把 ScriptResult 拍扁成给 LLM 看的纯文本. tail 4KB 已经够定位 traceback."""
    parts = [f"{action} {'成功' if result.success else '失败'} (exit={result.exit_code})"]
    if result.stdout:
        parts.append(f"\n--- stdout ---\n{result.stdout[-4096:]}")
    if result.stderr:
        parts.append(f"\n--- stderr ---\n{result.stderr[-4096:]}")
    return "".join(parts)


@tool(
    INSTALL_LIBS_TOOL_NAME,
    (
        "向当前 PentaLoom 共享 Python 环境装一组库 (内部跑 uv add). "
        "用户会被请求授权 — 同意后才会真装. "
        "参数: libs (要装的包名列表, PyPI 名, 比如 ['openpyxl', 'numpy>=1.26']), "
        "reason (向用户解释为什么需要这些包, 中文一句话)."
    ),
    {"libs": list[str], "reason": str},
)
async def _install_python_libs(args: dict[str, Any]) -> dict[str, Any]:
    """can_use_tool 通过后才会被 invoke. 跑 uv add, 把结果给 LLM."""
    libs = [lib.strip() for lib in (args.get("libs") or []) if str(lib).strip()]
    if not libs:
        return {
            "content": [{"type": "text", "text": "libs 为空, 没有可装的包."}],
            "is_error": True,
        }

    settings = get_settings()
    result = await python_env.install_libs(settings, libs)
    return {
        "content": [
            {
                "type": "text",
                "text": _format_script_result_text(f"uv add {' '.join(libs)}", result),
            }
        ],
        "is_error": not result.success,
    }


@tool(
    RUN_SCRIPT_TOOL_NAME,
    (
        "执行一个 Python 脚本 (用 PentaLoom 共享环境的 uv run python). "
        "用法: 先用 Write 把脚本写到 sandbox 或挂载目录里, 再调本工具把绝对路径传过来. "
        "用户会被请求授权. "
        "参数: script_path (脚本绝对路径), args (传给脚本的命令行参数, 可空), "
        "description (向用户解释脚本要干嘛, 中文一句话). "
        "默认 60s 超时."
    ),
    {"script_path": str, "args": list[str], "description": str},
)
async def _run_python_script(args: dict[str, Any]) -> dict[str, Any]:
    """can_use_tool 通过后才会被 invoke. 跑 uv run python <script>, 把结果给 LLM.

    路径白名单: script_path 必须落在 sandbox 或某个 mounted_dir 下 —
    防 LLM 拿这工具读 ~/.ssh/id_rsa 之类.
    """
    script_path_raw = str(args.get("script_path", "")).strip()
    if not script_path_raw:
        return {
            "content": [{"type": "text", "text": "script_path 不能为空."}],
            "is_error": True,
        }

    # 反 inline: 探到代码特征就回错, 让 LLM 改走 "Write 文件 → 传路径".
    # 加这一层是因为 LLM 偷懒倾向极强, 不拦住就会塞 "import x\nprint(...)"
    # 进来, 跑得了但 escape 灾难, 看到 traceback 也对不上行号.
    if _looks_like_inline_python(script_path_raw):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "run_python_script 只接受已存在的 .py 文件绝对路径, "
                        "不接受 inline 代码. 请先用 Write 把脚本写到 sandbox 里, "
                        "再把路径传过来."
                    ),
                }
            ],
            "is_error": True,
        }

    script_path = Path(script_path_raw).resolve()
    if not script_path.exists():
        return {
            "content": [
                {"type": "text", "text": f"脚本不存在: {script_path}"}
            ],
            "is_error": True,
        }
    if not script_path.is_file() or script_path.suffix != ".py":
        return {
            "content": [
                {"type": "text", "text": f"必须是 .py 文件: {script_path}"}
            ],
            "is_error": True,
        }

    cmd_args = [str(a) for a in (args.get("args") or [])]
    settings = get_settings()
    result = await python_env.run_script(settings, script_path, args=cmd_args)
    return {
        "content": [
            {
                "type": "text",
                "text": _format_script_result_text(
                    f"python {script_path.name}", result
                ),
            }
        ],
        "is_error": not result.success,
    }


# 这两个名字给 can_use_tool 拼 install_libs allowlist key 用.
PYTHON_ENV_TOOLS = (_install_python_libs, _run_python_script)
