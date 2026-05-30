"""Bocha (博查) search provider — 国内 web 搜索, 中文源覆盖好.

接口: POST https://api.bochaai.com/v1/web-search
鉴权: Authorization: Bearer <key>
免费试用: 1000 次, 注册 https://open.bochaai.com
"""

from __future__ import annotations

from typing import Any

from pentaloom.capabilities.search.providers.base import (
    SearchProvider,
    TextSearchResult,
)


class BochaProvider(SearchProvider):
    name = "bocha"
    endpoint = "https://api.bochaai.com/v1/web-search"

    # 时间窗: 我们的统一约定 d/w/m/y → Bocha 的 freshness 取值.
    _TIME_MAP = {
        "d": "oneDay",
        "w": "oneWeek",
        "m": "oneMonth",
        "y": "oneYear",
    }

    def __init__(self, api_key: str):
        self._api_key = api_key

    def build_request(
        self,
        query: str,
        *,
        max_results: int,
        timelimit: str | None,
        allowed_domains: list[str] | None,
        blocked_domains: list[str] | None,
        topic: str | None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {
            "query": query,
            "count": max_results,
            "summary": True,  # 拿长摘要做 body, 不开只有 snippet
        }
        if timelimit and timelimit in self._TIME_MAP:
            body["freshness"] = self._TIME_MAP[timelimit]
        # Bocha 没有原生 topic / 域名白黑名单字段, 这两个参数静默忽略
        # (跟 Tavily 一致的口径: 不支持的参数不报错, 上层 LLM 不必关心 provider 差异).
        _ = topic, allowed_domains, blocked_domains

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        return headers, body

    def parse_response(self, data: dict[str, Any]) -> list[TextSearchResult]:
        # 响应包了两层: 业务 code (与 HTTP 解耦) + data.webPages.value
        # 业务 code 非 200 (如 403 quota / 401 鉴权失败) 走这里也能解出空列表,
        # 但 service 层在 HTTP 200 + code 非 200 时直接抛 SearchError 更清晰 —
        # 这里只负责 happy path 提取.
        pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
        results: list[TextSearchResult] = []
        for p in pages:
            if not isinstance(p, dict):
                continue
            # summary=True 时优先用长摘要, 没拿到回落 snippet (短摘要).
            body = str(p.get("summary") or p.get("snippet") or "")
            results.append(
                TextSearchResult(
                    title=str(p.get("name") or ""),
                    href=str(p.get("url") or ""),
                    body=body,
                )
            )
        return results
