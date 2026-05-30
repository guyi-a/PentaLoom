"""顶层 search() 异步函数 — 按 region 路由 provider, 发请求, 解结果.

region 三档:
  - "global" → 单 Tavily (海外源, 英文为主)
  - "cn"     → 单 Bocha  (国内源, 中文为主)
  - "both"   → 两路并发, 按 URL 去重合并 (跨域话题用, 如 AI / 全球科技 / 跨境业务)

"both" 部分失败容忍: 一边挂另一边还能用; 两边都挂才抛 SearchError.
没配 key 的 provider 当 "失败" 处理 (返空, 不参与合并), 不阻塞另一边.

返回 list[TextSearchResult]:
  - 成功 → 结果列表 (可能为空, 比如冷僻 query).
  - 全部 provider 失败 → 抛 SearchError, 由调用方 (tools/search.py) 包成
    LLM 可读的 is_error 帧.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
from loguru import logger

from pentaloom.capabilities.search.providers.base import (
    SearchProvider,
    TextSearchResult,
)
from pentaloom.capabilities.search.providers.bocha import BochaProvider
from pentaloom.capabilities.search.providers.tavily import TavilyProvider
from pentaloom.config import get_settings

Region = Literal["cn", "global", "both"]


class SearchError(Exception):
    """搜索失败的统一基类. 由 tools 层捕获后转成 LLM-readable 的 is_error 帧."""


def _build_provider(name: str) -> SearchProvider | None:
    """按 provider 名构造实例; key 没配返 None (不抛, 让 both 模式能容忍单边缺 key)."""
    settings = get_settings()
    if name == "tavily":
        key = (settings.tavily_api_key or "").strip()
        return TavilyProvider(api_key=key) if key else None
    if name == "bocha":
        key = (settings.bocha_api_key or "").strip()
        return BochaProvider(api_key=key) if key else None
    return None


async def _run_one(
    provider: SearchProvider,
    query: str,
    *,
    max_results: int,
    timelimit: str | None,
    allowed_domains: list[str] | None,
    blocked_domains: list[str] | None,
    topic: str | None,
    timeout: float,
) -> list[TextSearchResult]:
    """单 provider 执行. 失败抛 SearchError (带 provider 名), 由 caller 决定吞还是抛."""
    headers, body = provider.build_request(
        query,
        max_results=max_results,
        timelimit=timelimit,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        topic=topic,
    )

    logger.info(f"[search:{provider.name}] query={query!r} max_results={max_results}")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(provider.endpoint, headers=headers, json=body)
    except httpx.TimeoutException as e:
        raise SearchError(f"{provider.name} 超时 ({timeout}s): {e}") from e
    except httpx.HTTPError as e:
        raise SearchError(f"{provider.name} 网络错误: {type(e).__name__}: {e}") from e

    if resp.status_code == 401:
        raise SearchError(f"{provider.name} 鉴权失败 (401) — 检查 key 是否过期 / 拼错")
    if resp.status_code == 403:
        raise SearchError(f"{provider.name} 配额不足 (403) — 免费额度可能没领取或已用完")
    if resp.status_code == 429:
        raise SearchError(f"{provider.name} 限流 (429) — 等会儿再试或升级套餐")
    if resp.status_code >= 400:
        raise SearchError(f"{provider.name} HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError as e:
        raise SearchError(f"{provider.name} 返回的不是 JSON: {e}") from e

    # Bocha 在 HTTP 200 时业务 code 可能非 200 (如 quota 用完返 200+403 in body).
    # 统一在这里早抛, 不让 parse_response 拿到空 pages 误判为"无结果".
    biz_code = data.get("code")
    if biz_code is not None and biz_code not in (200, "200"):
        msg = data.get("msg") or data.get("message") or "未知错误"
        raise SearchError(f"{provider.name} 业务错误 code={biz_code}: {msg}")

    try:
        results = provider.parse_response(data)
    except Exception as e:
        raise SearchError(f"{provider.name} 响应 parse 失败: {type(e).__name__}: {e}") from e

    logger.info(f"[search:{provider.name}] 返 {len(results)} 条")
    return results


def _merge_dedupe(
    *batches: list[TextSearchResult],
) -> list[TextSearchResult]:
    """按 href 去重合并多路结果. 先到先得 — 调用方按"优先级高的放前面"传入即可."""
    seen: set[str] = set()
    merged: list[TextSearchResult] = []
    for batch in batches:
        for r in batch:
            key = r.href or r.title  # 没 href 兜底用 title, 不至于无脑去重全删
            if key in seen:
                continue
            seen.add(key)
            merged.append(r)
    return merged


async def search(
    query: str,
    *,
    region: Region = "both",
    max_results: int = 10,
    timelimit: str | None = None,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    topic: str | None = None,
    timeout: float = 20.0,
) -> list[TextSearchResult]:
    """走 provider 调远端搜索, 返统一 TextSearchResult 列表.

    Args:
        query: 必填. 搜索关键字 (跟用户聊天语言保持一致即可).
        region: cn (Bocha 中文) / global (Tavily 海外) / both (并发合并, 默认).
        max_results: 单 provider 1-20. both 时合并去重后总数可能更少.
        timelimit: d/w/m/y = 最近 一天/一周/一月/一年.
        allowed_domains: 白名单 (跟 blocked_domains 互斥; 同传抛 SearchError).
                         注: Bocha 不支持域名过滤, 仅对 Tavily 生效.
        blocked_domains: 黑名单. 同上.
        topic: finance / news 等. 不在 provider 支持集会被静默忽略.
        timeout: HTTP 超时秒数.

    Raises:
        SearchError: 配置缺失 / 参数冲突 / 全部 provider 失败.
    """
    query = (query or "").strip()
    if not query:
        raise SearchError("query 不能为空")

    if allowed_domains and blocked_domains:
        raise SearchError("allowed_domains 和 blocked_domains 不能同时指定")

    if region not in ("cn", "global", "both"):
        raise SearchError(f"region 必须是 cn/global/both 之一, 收到 {region!r}")

    # 单 provider 上限校验 (both 时两边各传同一个 max_results, 合并后可能更少).
    capped_max = max(1, min(int(max_results or 10), 20))
    common_kwargs = dict(
        max_results=capped_max,
        timelimit=timelimit,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        topic=topic,
        timeout=timeout,
    )

    if region == "global":
        provider = _build_provider("tavily")
        if provider is None:
            raise SearchError(
                "未配置 TAVILY_API_KEY — region=global 需要 Tavily. "
                "去 https://app.tavily.com 注册免费 key 填到 agent/.env."
            )
        return await _run_one(provider, query, **common_kwargs)

    if region == "cn":
        provider = _build_provider("bocha")
        if provider is None:
            raise SearchError(
                "未配置 BOCHA_API_KEY — region=cn 需要 Bocha. "
                "去 https://open.bochaai.com 注册免费试用 key 填到 agent/.env."
            )
        return await _run_one(provider, query, **common_kwargs)

    # region == "both": 两路并发, 部分容忍.
    tavily = _build_provider("tavily")
    bocha = _build_provider("bocha")
    if tavily is None and bocha is None:
        raise SearchError(
            "region=both 但两个 provider 都没配 key. "
            "至少配 TAVILY_API_KEY 或 BOCHA_API_KEY 之一 (推荐都配)."
        )

    # 并发执行所有已配的 provider; gather 用 return_exceptions=True 让一边挂不影响另一边.
    tasks: list = []
    if bocha is not None:
        tasks.append(_run_one(bocha, query, **common_kwargs))
    if tavily is not None:
        tasks.append(_run_one(tavily, query, **common_kwargs))

    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    batches: list[list[TextSearchResult]] = []
    errors: list[str] = []
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            errors.append(str(outcome))
            logger.warning(f"[search:both] 一路失败: {outcome}")
        else:
            batches.append(outcome)

    if not batches:
        raise SearchError(f"region=both 两路全失败: {' | '.join(errors)}")

    # bocha 在 tasks list 里排第一 (如果存在), 合并时 Bocha 优先入榜 — 中文话题
    # 通常更切题; 海外话题 Bocha 没结果会自然把位置让给 Tavily.
    merged = _merge_dedupe(*batches)
    logger.info(
        f"[search:both] 合并 {sum(len(b) for b in batches)} 条 → 去重后 {len(merged)} 条"
        + (f" (部分失败: {len(errors)})" if errors else "")
    )
    return merged
