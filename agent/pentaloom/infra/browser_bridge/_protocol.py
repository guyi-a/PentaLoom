"""browser-bridge 协议常量.

扩展 (Kro Browser Bridge, Chrome 商店公开版) 硬编码以下值, server 必须**一字不差匹配**:

- /chrome-bridge/ping HTTP 端点 (GET / HEAD)
- /chrome-bridge/ws WebSocket 端点
- GET ping 必须带 X-Kro-Client-Sig: <PING_SIGNATURE> header (HEAD 不要求)

任意一处改了 → 扩展显示 disconnected, server 收不到任何流量.
"""

from __future__ import annotations

import hashlib

# ── ping 签名 ─────────────────────────────────────────────────
# 扩展把 sha256("kro-browser-bridge:kro-2026") 算出来放进 GET 请求 header,
# server 必须用同样的算法重算并比较. 公开扩展无法改这两个常量, server 端跟着固定.
PING_CLIENT_ID = "kro-browser-bridge"
PING_SALT = "kro-2026"
PING_SIGNATURE = hashlib.sha256(
    f"{PING_CLIENT_ID}:{PING_SALT}".encode()
).hexdigest()
PING_SIG_HEADER = "X-Kro-Client-Sig"

# ── HTTP 路径 ─────────────────────────────────────────────────
PING_PATH = "/chrome-bridge/ping"
WS_PATH = "/chrome-bridge/ws"

# ── 协议版本 / 标识 ───────────────────────────────────────────
# server 标识: 直接照搬 krow 的字符串, 扩展可能用它做 server 类型校验 (未查实).
SERVER_NAME = "krow-browser-bridge"
PROTOCOL_VERSION = "1.0"

# ── WebSocket 关闭码 ─────────────────────────────────────────
# 4401 = token 不对 / 鉴权失败 (扩展可能据此决定要不要重 ping 拿新 token).
WS_CLOSE_AUTH_FAILED = 4401
