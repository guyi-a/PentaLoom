"""browser-bridge — WebSocket 桥接到 Kro Browser Bridge Chrome 扩展.

routes:
  GET/HEAD /chrome-bridge/ping — 扩展定时探测, 返 token + ws path
  WS       /chrome-bridge/ws   — 扩展用 token 连接, 双向收发 command/result/event

模块布局:
  _protocol.py  — 硬编码常量 (路径 / ping 签名)
  models.py     — Pydantic
  registry.py   — 全局单例: 已连扩展 + 已上报 tabs
  service.py    — send_command + ~17 个 action 方法

进程级 (跨 PentaLoom session 共享): 扩展 = 用户的真实浏览器, 一个用户一个就够,
多个 chat tab 看到同一组 browser_id / pages.
"""

from pentaloom.infra.browser_bridge import service
from pentaloom.infra.browser_bridge._protocol import (
    PING_PATH,
    PING_SIG_HEADER,
    PING_SIGNATURE,
    SERVER_NAME,
    WS_CLOSE_AUTH_FAILED,
    WS_PATH,
)
from pentaloom.infra.browser_bridge.models import (
    BridgePingResponse,
    BrowserPage,
    BrowserSession,
    CommandEnvelope,
    EventEnvelope,
    ResultEnvelope,
)
from pentaloom.infra.browser_bridge.registry import BridgeClient, registry

__all__ = [
    "BridgeClient",
    "BridgePingResponse",
    "BrowserPage",
    "BrowserSession",
    "CommandEnvelope",
    "EventEnvelope",
    "PING_PATH",
    "PING_SIGNATURE",
    "PING_SIG_HEADER",
    "ResultEnvelope",
    "SERVER_NAME",
    "WS_CLOSE_AUTH_FAILED",
    "WS_PATH",
    "registry",
    "service",
]
