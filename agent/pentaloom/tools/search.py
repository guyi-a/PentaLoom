"""web_search in-process MCP 工具.

注册到独立 server "pentaloom_search". 完整工具名: mcp__pentaloom_search__web_search.

description 分两层:
  - @tool 的 description (MCP 协议字段, LLM 看工具清单时拿到): 写工具说明书 —
    干啥的 / 返什么 / 各参数含义和取值. 写充分, LLM 不用翻别处也能正确调.
  - 决策引导 (什么时候用 / 跟 browser 怎么分工 / 错了怎么兜底): 集中在
    prompts/tools.py 的 SEARCH_PROMPT_INSTRUCTIONS, 跟其他工具的"工具守则"共处.

HITL 策略:
  - 走单 key "enabled" 模式 (跟 browser_bridge / computer_use 对齐): 任何 query
    首次审批后, 整会话所有 web_search 调用免审.
  - 没配 TAVILY_API_KEY 时, service 抛 SearchError, 工具体返 is_error 给 LLM,
    agent 收到错误文案会知道该退到浏览器搜.
"""

from __future__ import annotations

import json
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from pentaloom.capabilities.search import SearchError, search

SEARCH_MCP_SERVER_NAME = "pentaloom_search"
WEB_SEARCH_TOOL_NAME = "web_search"

WEB_SEARCH_FULL_NAME = f"mcp__{SEARCH_MCP_SERVER_NAME}__{WEB_SEARCH_TOOL_NAME}"


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


@tool(
    WEB_SEARCH_TOOL_NAME,
    (
        "联网搜索. 返 [{title, href, body}, ...] JSON 列表 — "
        "title 是标题, href 是源链接, body 是内容摘要. "
        "参数: "
        "query (必填, 搜索关键字, 跟用户语言一致); "
        "region (cn=只搜国内 Bocha; global=只搜海外 Tavily; both=两路并发合并, 默认; "
        "明显国内话题用 cn 省 quota, 明显海外话题用 global 省 quota, 拿不准用 both); "
        "max_results (1-20, 默认 10, 太大 token 浪费; both 时是单 provider 上限, 合并后总数可能更少); "
        "timelimit (d/w/m/y = 最近 一天/一周/一月/一年); "
        "topic (finance/news, 不在支持集的值会被静默忽略不报错); "
        "allowed_domains / blocked_domains (域名字符串列表, 互斥, 只能用一个; 仅对 Tavily 生效)."
    ),
    {
        "query": str,
        "region": str,
        "max_results": int,
        "timelimit": str,
        "topic": str,
        "allowed_domains": list,
        "blocked_domains": list,
    },
)
async def _web_search(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return _err("query 不能为空")

    region = str(args.get("region") or "both").strip().lower()

    try:
        results = await search(
            query,
            region=region,  # type: ignore[arg-type]
            max_results=int(args.get("max_results") or 10),
            timelimit=args.get("timelimit") or None,
            topic=args.get("topic") or None,
            allowed_domains=args.get("allowed_domains") or None,
            blocked_domains=args.get("blocked_domains") or None,
        )
    except SearchError as e:
        return _err(f"search 失败: {e}")
    except Exception as e:
        return _err(f"search 未预期错误 {type(e).__name__}: {e}")

    payload = [r.model_dump() for r in results]
    if not payload:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"搜索 {query!r} 无结果. 可以换关键字再试, "
                        "或退到浏览器手动搜."
                    ),
                }
            ]
        }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ]
    }


SEARCH_MCP_SERVER = create_sdk_mcp_server(
    name=SEARCH_MCP_SERVER_NAME,
    tools=[_web_search],
)
