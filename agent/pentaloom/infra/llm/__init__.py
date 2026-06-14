"""内置 LLM 调用层.

给 PentaLoom 内部场景 (approval LLM classifier / 后续 summarize / 标题生成等)
提供统一的"调小模型"入口. 跟主对话的 ClaudeSDKClient 完全独立, 不污染主历史.

当前支持 DeepSeek (OpenAI 兼容 API). 远期扩 Qwen / GLM 等只需在 providers.py
注册表加一项.
"""
from .client import chat_complete
from .errors import LLMError, LLMInvalidResponse, LLMTimeout, LLMUnavailable

__all__ = [
    "chat_complete",
    "LLMError",
    "LLMUnavailable",
    "LLMTimeout",
    "LLMInvalidResponse",
]
