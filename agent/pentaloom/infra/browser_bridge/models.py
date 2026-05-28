"""browser-bridge 数据模型.

跟扩展之间通过 WebSocket JSON 帧交换. 三类业务帧 (command/result/event) + 握手
帧 (hello/hello_ack) + 错误帧 (error). 这些 envelope 在 router 里手工解析 dict,
模型主要给 Agent 工具回报和 registry 维护数据用.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class BridgePingResponse(BaseModel):
    """GET /chrome-bridge/ping 的响应体."""

    ok: bool = True
    server: str
    instanceId: str           # server 启动一次新生成, 扩展用来判断 server 重启
    protocolVersion: str
    wsPath: str
    token: str                # discovery_token, 扩展 WebSocket 连接时作为 query 参数


class BrowserSession(BaseModel):
    """list_sessions 返回里的一项 = 一个已连接的扩展实例."""

    browser_id: str
    label: str
    last_seen_at: int         # 毫秒时间戳


class BrowserPage(BaseModel):
    """一个被扩展上报的浏览器标签页. registry 按 (browser_id, tab_id) 去重."""

    page_id: str              # server 生成, "page_<hex>" 格式
    browser_id: str
    window_id: int
    tab_id: int               # 扩展上报的 chrome.tabs.Tab.id
    url: str
    title: str
    active: bool
    controllable: bool = True
    context_role: str | None = None
    last_seen_at: int


class CommandEnvelope(BaseModel):
    """server → 扩展. id 用来跟 ResultEnvelope 配对."""

    type: Literal["command"] = "command"
    id: str
    session_id: str
    timestamp: int
    payload: dict             # {"tool": "browser_xxx", "arguments": {...}}


class ResultEnvelope(BaseModel):
    """扩展 → server. id 跟 CommandEnvelope 配对."""

    type: Literal["result"] = "result"
    id: str
    payload: dict


class EventEnvelope(BaseModel):
    """扩展 → server. 主动推送 (page_updated / page_closed / page_removed 等)."""

    type: Literal["event"] = "event"
    payload: dict
