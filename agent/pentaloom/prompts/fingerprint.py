"""prompt 指纹 — 启动时打 debug 日志, 改了 prompts/ 任意文件指纹就变."""

from __future__ import annotations

import hashlib

from loguru import logger


def summarize(prompt: str, sections: list[str]) -> dict[str, object]:
    """给 prompt 算个 16 hex 摘要 + 长度, 返回结构化 dict 给日志用."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return {
        "fingerprint": digest,
        "length": len(prompt),
        "sections": sections,
    }


def log(prompt: str, sections: list[str]) -> None:
    """在 PentaLoom 启动时调一次. 默认 logger debug 级别, 不噪声."""
    info = summarize(prompt, sections)
    logger.debug(
        f"[prompts] main_prompt fingerprint={info['fingerprint']} "
        f"length={info['length']} sections={info['sections']}"
    )
