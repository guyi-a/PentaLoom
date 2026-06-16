"""loom_client — Python 端调 loom daemon 的 Unix socket client.

loom (Go binary) 是 weaver invocable app **window** 类型的 host daemon, listen
~/.pentaloom/loom.sock, JSON-line 协议. PentaLoom 后端通过这个 client 给 loom
发 window.open / window.close / window.list 命令, loom 收到后 spawn / kill loomer
子进程渲窗.

跟 Go 端 loom/internal/socket/socket.go Send() 行为对位 — 一次 connect + 一行
JSON request + 一行 JSON response + 关 conn. 简单同步调用.

socket 不通 / loom daemon 没起 → 抛 LoomUnavailable, 调用方自己决定怎么 fallback
(routers/weaver.py 的 /window/open 应该返 503 + 提示用户跑 make loom-install).
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any


class LoomError(RuntimeError):
    """loom 调用失败的基类."""


class LoomUnavailable(LoomError):
    """loom socket 不存在 / 连不上 (daemon 没起). 调用方应 fallback / 提示用户."""


class LoomCommandFailed(LoomError):
    """loom 收到了请求但执行失败 (e.g. window not found). 业务错误, 不要重试."""


def default_socket_path() -> Path:
    """跟 Go 端 cli.go defaultSocketPath() 同款: ~/.pentaloom/loom.sock."""
    return Path(os.path.expanduser("~/.pentaloom/loom.sock"))


def is_available(socket_path: Path | None = None) -> bool:
    """快速探测 loom socket 是否可用 (PentaLoom 启动时检查用).

    只 stat 文件, 不真 connect — 不在乎 daemon 是否还接得住, 只看 socket 文件
    存在. 如果 socket 文件在但 daemon 死了, 实际发命令时会拿到 LoomUnavailable.
    """
    p = socket_path or default_socket_path()
    return p.exists()


async def call(
    cmd: str,
    data: dict[str, Any] | None = None,
    *,
    socket_path: Path | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """发一个命令给 loom daemon, 返 daemon 返的 data 字段 (业务 payload).

    Args:
        cmd: e.g. "window.open" / "window.close" / "window.list"
        data: cmd 对应的 payload (会 JSON 序列化进 Request.data)
        socket_path: 默认 ~/.pentaloom/loom.sock
        timeout: 整次 (connect + write + read) 上限 (s)

    Returns:
        daemon 返的 Response.data 字段 (已 JSON 解析). cmd 不返业务 data 时是 {}.

    Raises:
        LoomUnavailable: 连不上 daemon
        LoomCommandFailed: daemon 返 Ok=false
        LoomError: 协议错 / 超时
    """
    p = socket_path or default_socket_path()
    if not p.exists():
        raise LoomUnavailable(
            f"loom socket {p} 不存在 — daemon 没起. 跑 `make loom-install` 装系统级 "
            f"daemon, 或 `make loom-dev` 起开发态."
        )

    req = {
        "id": secrets.token_hex(4),
        "cmd": cmd,
    }
    if data is not None:
        req["data"] = data
    payload = json.dumps(req, ensure_ascii=False).encode("utf-8") + b"\n"

    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_unix_connection(str(p))
            try:
                writer.write(payload)
                await writer.drain()
                line = await reader.readline()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
    except (FileNotFoundError, ConnectionRefusedError) as e:
        # daemon 起不来 / socket 文件死了 / 不接连接.
        raise LoomUnavailable(f"loom socket connect failed: {e}") from e
    except asyncio.TimeoutError as e:
        raise LoomError(f"loom call {cmd!r} timeout after {timeout}s") from e
    except OSError as e:
        raise LoomUnavailable(f"loom socket OS error: {e}") from e

    if not line:
        raise LoomError(f"loom returned empty response for {cmd!r}")

    try:
        resp = json.loads(line.decode("utf-8").strip())
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise LoomError(f"loom returned malformed response: {e}; raw: {line!r}") from e

    if not resp.get("ok"):
        raise LoomCommandFailed(resp.get("error") or f"loom {cmd} returned ok=false")
    return resp.get("data") or {}


# ────────────────────────────────────────────────────────────────────
# 高层包装 — 跟 loom Go 端 cmd 名字字字对应, 给 router / app_runtime 用.
# ────────────────────────────────────────────────────────────────────


async def open_window(
    entry_path: str,
    *,
    title: str = "",
    width: int = 0,
    height: int = 0,
    app: str = "",
    window_name: str = "",
) -> dict[str, Any]:
    """开一个 window. 返 {"window_id": str, "pid": int}.

    app + window_name 给 loom registry 做二级索引, 后续 close-by-name / invoke
    都靠它找回这个窗. 不传的话 window 还是开得了, 但反向调通路全废 (registry
    findByName 找不到), 所以走 invocable app 路径的调用方必填这俩.
    """
    return await call("window.open", {
        "entry_path": entry_path,
        "title": title,
        "width": width,
        "height": height,
        "app": app,
        "window_name": window_name,
    })


async def close_window(window_id: str) -> None:
    """关一个 window. window_id 不存在抛 LoomCommandFailed."""
    await call("window.close", {"window_id": window_id})


async def list_windows() -> list[dict[str, Any]]:
    """列已开 windows. 返 [{"window_id", "pid", "entry_path", "title", "started_at"}, ...]."""
    data = await call("window.list", {})
    return data.get("windows") or []


async def invoke_window(
    app: str,
    window_name: str,
    invocation_id: str,
    args: dict[str, Any] | None = None,
    *,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """给已开的 window 推一个 invocation, 等 JS 端 handler 返结果.

    协议路径:
      Python → loom socket window.invoke
        → loom 找 (app, window_name) 对应 loomer 子进程
        → 写 NDJSON 到 loomer stdin
        → loomer 主线程 webview.Eval 调 JS handler (window.pentaloom.registerInvocation 注册的)
        → JS handler async 完了通过 __pentaloom_handler_result 回 Go
        → loomer 写 result NDJSON 到 stdout
        → loom 路由回 pending channel
        → socket response → Python

    Args:
        app: weaver app 名
        window_name: app.json components.windows[].name (二级索引)
        invocation_id: manifest invocations[].id
        args: 给 handler 的 payload, 序列化进 args 字段
        timeout_s: 整次 invoke 超时 (含协议往返). loom 内部默认 30s, 这里覆盖.

    Returns:
        JS handler 的返值 (任意 JSON value 包成 dict — 若 handler 返 primitive,
        包成 {"value": <primitive>} 防上层 schema 校验崩). 实测 handler 返
        object/array 是常态, primitive 退化路径.

    Raises:
        LoomUnavailable: loom daemon 没起
        LoomCommandFailed: window 没开 / handler 没注册 / handler 抛错 / loom 端超时
        LoomError: socket 协议错
    """
    # socket 层超时给 loom 内部超时留 1s buffer — loom 端 select 超时返 socket
    # response 也要时间. 整体上 timeout_s + 1s.
    data = await call(
        "window.invoke",
        {
            "app": app,
            "window_name": window_name,
            "invocation_id": invocation_id,
            "args": args or {},
            "timeout_ms": int(timeout_s * 1000),
        },
        timeout=timeout_s + 1.0,
    )
    output = data.get("output")
    if output is None:
        return {}
    if isinstance(output, dict):
        return output
    # primitive / list — 包一层, 调用方按 output["value"] 拿
    return {"value": output}
