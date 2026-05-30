"""Tavily search provider — 直连 api.tavily.com, key 从 settings 读.

接口: POST https://api.tavily.com/search
鉴权: Authorization: Bearer <key>
免费额度: 1000 次/月, 注册 https://app.tavily.com
"""

from __future__ import annotations

from typing import Any

from pentaloom.capabilities.search.providers.base import (
    SearchProvider,
    TextSearchResult,
)


class TavilyProvider(SearchProvider):
    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    # 时间窗约定: d/w/m/y = 最近一天 / 一周 / 一月 / 一年.
    _TIME_MAP = {"d": "day", "w": "week", "m": "month", "y": "year"}

    # Tavily 支持的 topic 集; 不在集内静默忽略, 不报错 (省得 agent 因小参数失败).
    _SUPPORTED_TOPICS = {"finance", "news"}

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
            "max_results": max_results,
            "search_depth": "basic",   # advanced 双倍 credits, basic 对一般问题够了
            "include_answer": "basic", # Tavily 自带 LLM 总结一句, 可作兜底
        }
        if topic and topic in self._SUPPORTED_TOPICS:
            body["topic"] = topic
        if timelimit and timelimit in self._TIME_MAP:
            body["time_range"] = self._TIME_MAP[timelimit]
        if allowed_domains:
            body["include_domains"] = allowed_domains
        if blocked_domains:
            body["exclude_domains"] = blocked_domains

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        return headers, body

    def parse_response(self, data: dict[str, Any]) -> list[TextSearchResult]:
        results = data.get("results") or []
        return [
            TextSearchResult(
                title=str(r.get("title") or ""),
                href=str(r.get("url") or ""),
                body=str(r.get("content") or ""),
            )
            for r in results
            if isinstance(r, dict)
        ]
