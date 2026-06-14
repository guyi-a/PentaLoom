"""内置 LLM 调用的异常类型.

调用方 (e.g. approval classifier) 用这些区分 "没配 key" / "超时" / "API 错"
来决定是 deny / 重试 / 报警.
"""
from __future__ import annotations


class LLMError(RuntimeError):
    """所有内置 LLM 错误的基类."""


class LLMUnavailable(LLMError):
    """provider 没注册, 或者注册了但 api_key 为空. 调用方应 fall back, 不重试."""


class LLMTimeout(LLMError):
    """超时. 调用方决定要不要重试."""


class LLMInvalidResponse(LLMError):
    """API 返了, 但 content 是空的 / 格式不对. 调用方一般 fall back."""
