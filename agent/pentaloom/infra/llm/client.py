"""通用 LLM 调用 — chat_complete.

跟 ClaudeSDKClient 完全独立: stateless, 每次调新建 openai client, 不接 LoomPool.

支持 response_format='json' (DeepSeek / OpenAI 都接 {"type": "json_object"}).
返 raw str, caller 自己 json.loads — 这层不解析, 留给业务方决定 schema.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

import openai
from loguru import logger

from pentaloom.config import get_settings

from .errors import LLMError, LLMInvalidResponse, LLMTimeout
from .providers import resolve_model


async def chat_complete(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    response_format: Literal["text", "json"] = "text",
    timeout_s: float = 30.0,
) -> str:
    """调内置 LLM, 返 message content 字符串.

    Args:
        messages: OpenAI chat 格式 [{"role": "system|user|assistant", "content": ...}].
        model: 模型名 (e.g. "deepseek-chat"). None 走 settings.internal_llm_model.
        max_tokens: 输出上限.
        temperature: 0 做分类 / 1 做创意.
        response_format: "json" 时强制返合法 JSON 字符串.
        timeout_s: 整次调用 (含网络 + 推理) 超时上限.

    Raises:
        LLMUnavailable: model 没注册或 api_key 为空.
        LLMTimeout: 超时.
        LLMError: API 报错 / 其他 openai 异常.
        LLMInvalidResponse: 返了但 content 空.
    """
    if model is None:
        model = get_settings().internal_llm_model
    base_url, api_key, model_name = resolve_model(model)

    client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)
    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}

    try:
        completion = await asyncio.wait_for(
            client.chat.completions.create(**kwargs),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as e:
        raise LLMTimeout(f"chat_complete timed out after {timeout_s}s model={model_name}") from e
    except openai.OpenAIError as e:
        raise LLMError(f"chat_complete failed model={model_name}: {e}") from e

    if not completion.choices:
        raise LLMInvalidResponse(f"empty choices model={model_name}")
    text = completion.choices[0].message.content
    if not text:
        raise LLMInvalidResponse(f"empty content model={model_name}")
    logger.debug(
        f"chat_complete ok model={model_name} in_tokens={getattr(completion.usage, 'prompt_tokens', '?')} "
        f"out_tokens={getattr(completion.usage, 'completion_tokens', '?')}"
    )
    return text
