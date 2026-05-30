"""联网搜索能力. 当前只接 Tavily, 未来加 provider 走 providers/ 子目录."""

from pentaloom.capabilities.search.providers.base import (
    SearchProvider,
    TextSearchResult,
)
from pentaloom.capabilities.search.service import (
    SearchError,
    search,
)

__all__ = [
    "SearchError",
    "SearchProvider",
    "TextSearchResult",
    "search",
]
