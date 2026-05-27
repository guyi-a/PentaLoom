"""file_read / file_verify 共用的 Pydantic 出参模型.

设计原则: 给 LLM 看的字段尽量"自解释" — message 自带上下文 ("Slide 3 / Run 2:"),
不依赖前端额外渲染. 二进制 / 大对象不入模型, 报路径就够.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["blocking", "warning"]
Tier = Literal["tier1", "tier2", "tier3"]


class FileWarning(BaseModel):
    """读文件时的非致命提示 — 表示文件能读但有问题 (如 truncated / missing EA font)."""

    kind: str
    message: str
    location: str | None = None


class FileReadResult(BaseModel):
    """file_read 工具的统一出参.

    text: 已渲染好的 markdown / plain — agent 直接消化.
    warnings: 截断 / 内容异常的提示, agent 看到后可决定是否调 file_verify.
    meta: 文件维度数据 (页数 / sheet 数等), 给 agent "下一步要不要分块读" 参考.
    """

    path: str
    kind: Literal["docx", "pptx", "xlsx"]
    text: str
    warnings: list[FileWarning] = Field(default_factory=list)
    meta: dict[str, int | str | list[str] | list[int]] = Field(default_factory=dict)


class Issue(BaseModel):
    """file_verify 报告的一条问题."""

    tier: Tier
    kind: str
    severity: Severity
    location: str
    message: str


class VerifyReport(BaseModel):
    """file_verify 工具的统一出参.

    ok: blocking_count == 0 (autofix 跑完后).
    fixes_applied: 字符串列表, 表示这次实际做了哪些修复 (如 ["fonts", "empty_pages"]).
    """

    path: str
    kind: Literal["pdf", "pptx"]
    ok: bool
    blocking_count: int
    warning_count: int
    fixes_applied: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
