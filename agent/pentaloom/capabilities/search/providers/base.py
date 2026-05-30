"""Provider 抽象 + 统一结果模型.

加新 provider (Bocha / Brave / ...) 只要继承 SearchProvider 实现 build_request +
parse_response, service.search 根据 settings 选哪个不动业务代码.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class TextSearchResult(BaseModel):
    """一条文本搜索结果. 3 字段最小够用, 足够 agent 决策"看完就用"还是"打开链接深读"."""

    title: str
    href: str
    body: str


class SearchProvider(ABC):
    name: str
    endpoint: str

    @abstractmethod
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
        """返回 (headers, json_body), 用 httpx POST endpoint."""

    @abstractmethod
    def parse_response(self, data: dict[str, Any]) -> list[TextSearchResult]:
        ...
