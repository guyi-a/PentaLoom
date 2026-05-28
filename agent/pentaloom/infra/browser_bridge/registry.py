"""browser-bridge 全局状态注册表.

进程级单例 — 多 PentaLoom session 共享同一组连上的扩展 (bridge 是 user-scoped
的: 一个用户的 Chrome 通常就一个, 多个 chat tab 都能看到同一个 browser_id /
同一组 pages, 这是设计上想要的).

两层 dict:
  clients: session_id → BridgeClient  (一个 WebSocket 连接 = 一个 client)
  clients_by_browser: browser_id → BridgeClient  (扩展上报的稳定 ID)
  pages: page_id → BrowserPage  (扩展通过 page_updated 事件维护)

session_id 是 server 分配的 (跨重连变), browser_id 是扩展自己生成存
chrome.storage 里的 (跨重连稳定). hello 帧把 browser_id 覆盖到 client 上.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from pentaloom.infra.browser_bridge._protocol import (
    PROTOCOL_VERSION,
    SERVER_NAME,
    WS_PATH,
)
from pentaloom.infra.browser_bridge.models import BrowserPage, BrowserSession


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class BridgeClient:
    """对应一条 WebSocket 连接 + 它身上的扩展元信息."""

    session_id: str
    browser_id: str
    label: str
    extension_version: str | None = None
    connected_at: int = field(default_factory=_now_ms)
    last_seen_at: int = field(default_factory=_now_ms)


class BrowserBridgeRegistry:
    def __init__(self) -> None:
        # server 启动一次, 给扩展判断"server 重启了"
        self.instance_id = str(uuid.uuid4())
        # 扩展 ping 后拿到的 token, 用作 WS 连接鉴权 query 参数
        self.discovery_token = uuid.uuid4().hex
        self.clients: dict[str, BridgeClient] = {}
        self.clients_by_browser: dict[str, BridgeClient] = {}
        self.pages: dict[str, BrowserPage] = {}

    def ping_payload(self) -> dict:
        return {
            "ok": True,
            "server": SERVER_NAME,
            "instanceId": self.instance_id,
            "protocolVersion": PROTOCOL_VERSION,
            "wsPath": WS_PATH,
            "token": self.discovery_token,
        }

    def register_client(self, browser_label: str) -> BridgeClient:
        """WS accept 后立刻调. browser_id 先用临时随机值, hello 来了再 set_browser_id 覆盖."""
        session_id = uuid.uuid4().hex
        browser_id = uuid.uuid4().hex
        client = BridgeClient(
            session_id=session_id, browser_id=browser_id, label=browser_label
        )
        self.clients[session_id] = client
        self.clients_by_browser[browser_id] = client
        return client

    def unregister_client(self, session_id: str) -> None:
        client = self.clients.pop(session_id, None)
        if client is None:
            return
        self.clients_by_browser.pop(client.browser_id, None)
        # 顺带把这个 browser_id 下的 pages 全清, 防止后续 list_pages 给到陈旧数据
        stale = [pid for pid, p in self.pages.items() if p.browser_id == client.browser_id]
        for pid in stale:
            self.pages.pop(pid, None)

    def set_browser_id(
        self,
        session_id: str,
        browser_id: str,
        *,
        browser_label: str | None = None,
        extension_version: str | None = None,
    ) -> BridgeClient | None:
        """hello 帧来了调. 用扩展上报的稳定 browser_id 覆盖临时随机值."""
        client = self.clients.get(session_id)
        if client is None:
            return None
        # 旧 browser_id 从反向索引清掉
        self.clients_by_browser.pop(client.browser_id, None)
        client.browser_id = browser_id
        if browser_label:
            client.label = browser_label
        if extension_version:
            client.extension_version = extension_version
        client.last_seen_at = _now_ms()
        self.clients_by_browser[browser_id] = client
        return client

    def get_client_by_browser(self, browser_id: str) -> BridgeClient | None:
        return self.clients_by_browser.get(browser_id)

    def list_sessions(self) -> list[BrowserSession]:
        return [
            BrowserSession(
                browser_id=c.browser_id, label=c.label, last_seen_at=c.last_seen_at
            )
            for c in self.clients.values()
        ]

    def upsert_page(
        self,
        *,
        browser_id: str,
        window_id: int,
        tab_id: int,
        url: str,
        title: str,
        active: bool,
        context_role: str | None = None,
    ) -> BrowserPage:
        """page_updated event 来了调. 按 (browser_id, tab_id) 去重, 有就 update."""
        now = _now_ms()
        existing = next(
            (
                p
                for p in self.pages.values()
                if p.browser_id == browser_id and p.tab_id == tab_id
            ),
            None,
        )
        if existing:
            existing.window_id = window_id
            existing.url = url
            existing.title = title
            existing.active = active
            existing.context_role = context_role
            existing.last_seen_at = now
            return existing
        page = BrowserPage(
            page_id=f"page_{uuid.uuid4().hex}",
            browser_id=browser_id,
            window_id=window_id,
            tab_id=tab_id,
            url=url,
            title=title,
            active=active,
            context_role=context_role,
            last_seen_at=now,
        )
        self.pages[page.page_id] = page
        return page

    def remove_page(self, *, browser_id: str, tab_id: int) -> None:
        stale = [
            pid
            for pid, p in self.pages.items()
            if p.browser_id == browser_id and p.tab_id == tab_id
        ]
        for pid in stale:
            self.pages.pop(pid, None)

    def get_page(self, browser_id: str, page_id: str) -> BrowserPage | None:
        page = self.pages.get(page_id)
        if page and page.browser_id == browser_id:
            return page
        return None

    def list_pages(self, browser_id: str) -> list[BrowserPage]:
        return [p for p in self.pages.values() if p.browser_id == browser_id]


# 进程级单例. 多 session / 多请求都用这个.
registry = BrowserBridgeRegistry()
