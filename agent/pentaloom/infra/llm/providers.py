"""Provider 注册表 + 模型路由.

按模型名找 (base_url, api_key, model). API key 走 settings 上的字段
(底层从 .env 读). 不读 settings.json — 这层是基础设施, 不该依赖用户写的 JSON.

新加 provider: 在 _PROVIDERS 加一项 + settings.py 加对应 api_key 字段.
"""
from __future__ import annotations

from dataclasses import dataclass

from pentaloom.config import get_settings

from .errors import LLMUnavailable


@dataclass(frozen=True, slots=True)
class _ProviderConfig:
    base_url: str
    api_key_field: str  # Settings dataclass 上的字段名 (e.g. "deepseek_api_key")
    models: tuple[str, ...]


_PROVIDERS: dict[str, _ProviderConfig] = {
    "deepseek": _ProviderConfig(
        base_url="https://api.deepseek.com",
        api_key_field="deepseek_api_key",
        models=("deepseek-chat", "deepseek-reasoner"),
    ),
}


def resolve_model(model_name: str) -> tuple[str, str, str]:
    """按 model_name 路由到 provider, 返 (base_url, api_key, resolved_model_name).

    raises LLMUnavailable: model 没注册, 或注册了但 api_key 字段为空.
    """
    settings = get_settings()
    for provider_name, cfg in _PROVIDERS.items():
        if model_name in cfg.models:
            api_key = getattr(settings, cfg.api_key_field, "") or ""
            if not api_key:
                raise LLMUnavailable(
                    f"provider {provider_name!r} model={model_name!r} 缺 api_key "
                    f"(settings.{cfg.api_key_field}). 在 agent/.env 加 "
                    f"DEEPSEEK_API_KEY=... (或对应 provider 的环境变量)"
                )
            return cfg.base_url, api_key, model_name
    raise LLMUnavailable(
        f"unknown model {model_name!r}, 已注册 provider+models: "
        f"{ {k: list(v.models) for k, v in _PROVIDERS.items()} }"
    )


def list_available_models() -> list[str]:
    """所有有 api_key 的可用模型. 给前端 / 调试 / 健康检查用."""
    settings = get_settings()
    out: list[str] = []
    for cfg in _PROVIDERS.values():
        if getattr(settings, cfg.api_key_field, ""):
            out.extend(cfg.models)
    return out
