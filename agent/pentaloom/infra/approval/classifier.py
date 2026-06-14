"""LLM-based 工具调用裁判 — 给 AutoPolicy 兜底.

调用方: AutoPolicy.should_auto_approve(). harmless 白名单没命中且非 destructive
时调进来, 让小模型判一次"这个动作要不要 block".

策略:
  - 失败一律 fall back 到 (False, <reason>) 偏严 — 用户多点一次 vs 文件被破坏,
    永远选前者.
  - args truncate 防 prompt injection (模型上下文里塞大段恶意文本可能盖掉
    system prompt).
  - response_format=json 让模型必返合法 JSON, 简化解析.
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from pentaloom.infra.llm import (
    LLMError,
    LLMInvalidResponse,
    LLMTimeout,
    LLMUnavailable,
    chat_complete,
)


_SYSTEM_PROMPT = """\
You are a security classifier for an AI coding agent. Your job is to decide \
whether a tool call should be ALLOWED (auto-approved) or BLOCKED (needs human review).

## Classification Process

1. Identify what the action does concretely (read, write, execute, network, etc.).
2. Check if all file paths are within the workspace or user-granted directories.
3. Assess reversibility — can the user undo this without data loss?
4. Check for external side effects — does it touch systems outside the local machine?
5. When in doubt, BLOCK. False positives (unnecessary blocks) are far less costly \
than false negatives (allowing harmful actions).

## ALLOW criteria (all must be true)

- Action is non-destructive: it does not delete, overwrite, or corrupt existing \
files, and does not kill or alter running processes or system state
- Action is reversible or low-impact (e.g. reading files, listing directories, \
running tests, installing dev dependencies, git operations within the repo)
- No external side effects (no network requests to external services, no deployment, \
no publishing)
- No sensitive data access (no credentials, secrets, SSH keys, auth tokens)

## Path scope rules

- Actions within the workspace or user-granted paths are always allowed (if non-destructive).
- Read-only access outside the workspace (ls, cat, head, tail, find, grep, stat, \
wc, file, du, df, etc.) is ALLOWED — reading files outside the workspace is safe \
as long as it does not touch sensitive data (credentials, secrets, etc.).
- Write or destructive operations outside the workspace are BLOCKED — these include \
creating, modifying, deleting, or moving files outside the workspace.

## BLOCK criteria (any one is sufficient)

- Destructive operations: rm, shred, truncating files with > redirect, \
DROP TABLE, git clean, git reset --hard, kill, killall, pkill, or any action \
that deletes/overwrites existing files or stops running processes
- Code from external sources: curl | bash, wget + execute, eval of remote content
- Credential / secret access: reading .env files outside workspace, SSH keys, \
cloud credentials, browser cookies/passwords
- External communication: sending emails, posting to APIs, webhooks, Slack messages, \
HTTP requests to non-localhost endpoints
- Production / deployment actions: docker push, npm publish, deploy commands, \
modifying CI/CD pipelines
- Security weakening: chmod 777, disabling firewalls, modifying auth configs
- Unauthorized persistence: modifying shell profiles (.bashrc, .zshrc), cron jobs, \
startup scripts, launch agents
- Writing outside workspace: creating, modifying, or moving files outside the \
workspace and user-granted directories

## Important Notes

- Shell commands that only READ data are safe regardless of path scope \
(e.g. ls, cat, grep, find, head, tail, wc, file, du, stat, git log, git diff).
- Shell commands that WRITE within the workspace are generally safe \
(e.g. mkdir, touch, npm install, pip install, git add, git commit).
- Package install commands (npm install, pip install, cargo build) are safe when \
they don't use --global or install outside workspace.
- Git push is BLOCK — it has external side effects.
- Explicit user confirmation in the conversation (not suggestive or implied) \
can override a block, but you are not given conversation history, so you \
cannot verify this. Default to blocking if unsure.

## Output

Reply with a JSON object only, no markdown code fence:
  {"thinking": "<short reason in <=120 chars>", "should_block": true|false}
"""


_USER_PROMPT_TEMPLATE = """\
Classify this tool call:

Environment:
- Workspace: {workspace_dir}

Action:
- Tool: {tool_name}
- Arguments:
{args_summary}
"""


def _truncate_args(args: dict[str, Any], max_len: int = 800) -> str:
    """JSON 化 + 截断 — 防 args 里塞大段文本盖掉 system prompt.

    特殊处理 'content' 字段 (可能是巨大文件内容): 单独截到 200 字符再 JSON 化.
    """
    truncated = dict(args)
    content = truncated.get("content")
    if isinstance(content, str) and len(content) > 200:
        truncated["content"] = content[:200] + f"... ({len(content)} chars total)"
    s = json.dumps(truncated, ensure_ascii=False, default=str)
    if len(s) > max_len:
        s = s[:max_len] + "..."
    return s


async def classify_with_llm(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    workspace_dir: str | None = None,
    timeout_s: float = 15.0,
) -> tuple[bool, str | None]:
    """让 LLM 裁判一个工具调用. 返 (approved, reason).

    approved=True: 模型说可以放行.
    approved=False + reason: 走人工审, reason 是简短说明 (LLM thinking 或失败原因).

    所有失败路径 (没 key / 超时 / API 错 / JSON 不对 / content 空) 都 fall back
    到 (False, "<reason>") — 偏严不偏松.
    """
    args_summary = _truncate_args(tool_input)
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        workspace_dir=workspace_dir or "(not initialized)",
        tool_name=tool_name,
        args_summary=args_summary,
    )

    try:
        text = await chat_complete(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=512,
            temperature=0.0,
            response_format="json",
            timeout_s=timeout_s,
        )
    except LLMUnavailable as e:
        logger.warning(f"classify_with_llm unavailable, deny: {e}")
        return (False, "missing_api_key")
    except LLMTimeout:
        logger.warning(f"classify_with_llm timeout tool={tool_name}")
        return (False, "classifier_timeout")
    except (LLMError, LLMInvalidResponse) as e:
        logger.warning(f"classify_with_llm error tool={tool_name}: {e}")
        return (False, "classifier_error")

    # 解析 JSON. response_format=json 强制模型返合法 JSON, 但兜底也加 try/except.
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"classify_with_llm bad JSON tool={tool_name}: {e}; text={text[:200]!r}")
        return (False, "classifier_bad_json")

    if not isinstance(data, dict):
        return (False, "classifier_bad_json")

    should_block = bool(data.get("should_block", True))
    thinking = str(data.get("thinking") or "").strip()

    if should_block:
        # 把 LLM 的 thinking 短摘进 reason, 给前端展示用 (远期).
        reason = f"llm:{thinking[:120]}" if thinking else "llm_blocked"
        return (False, reason)
    return (True, "llm_allowed")
