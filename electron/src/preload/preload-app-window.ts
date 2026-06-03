/**
 * PentaLoom weaver app window preload (Phase C-1 + C-2).
 *
 * 跟主 shell preload 分开 — 主壳 preload 暴露的是给 React renderer 用的 openAppWindow
 * 等 IPC; 这个 preload 是给 weaver app HTML 用的 runtime API.
 *
 * 暴露到 window.pentaloom:
 *   - appName: 当前 app 名 (用户写 window HTML 时可能想知道自己是谁)
 *   - invokeApp(id, args?):                Phase C-1 — window → script (HTTP POST)
 *   - registerInvocation(id, handler):     Phase C-2 — window 暴露 handler 给 agent
 *                                          (agent invoke_app target=window 路由到这)
 *
 * appName 由 main.ts 创建 BrowserWindow 时通过 additionalArguments 传:
 *   '--pentaloom-app-name=<name>'
 *
 * Phase C-2 通信: 这个 preload 启动时建 WebSocket 到 ws://localhost:8090/weaver/apps/<name>/window-ws.
 * 后端推 {type:invoke, request_id, invocation_id, args} → preload 查 handler → 调 → ws send
 * {type:invoke_result, request_id, output} 或 invoke_error.
 *
 * 安全: 这个 preload 暴露的 API 走同 origin fetch + WS (loadURL 已经是 http://localhost:8090),
 * 不暴露任何 Electron / Node API. JS 拿不到 ipcRenderer.
 */

import { contextBridge } from 'electron';

const NAME_PREFIX = '--pentaloom-app-name=';
const API_BASE_PREFIX = '--pentaloom-api-base=';

function readArg(prefix: string): string {
  const arg = process.argv.find((a) => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : '';
}

const appName = readArg(NAME_PREFIX);
const apiBase = readArg(API_BASE_PREFIX) || 'http://localhost:8090';

// ─── Phase C-1: invokeApp (window → script) ────────────────────

async function invokeApp(
  invocationId: string,
  args?: Record<string, unknown>,
): Promise<unknown> {
  if (!appName) {
    throw new Error('pentaloom: appName not injected (preload misconfigured)');
  }
  if (!invocationId || typeof invocationId !== 'string') {
    throw new Error('pentaloom.invokeApp: invocation_id required (string)');
  }
  const url = `${apiBase}/weaver/apps/${encodeURIComponent(appName)}/invoke`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      invocation_id: invocationId,
      args: args ?? {},
    }),
  });
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j?.detail) detail = String(j.detail);
    } catch {
      /* 非 JSON 错误, 用 status text */
    }
    throw new Error(`invoke_app failed: ${detail}`);
  }
  return r.json();
}

// ─── Phase C-2: registerInvocation (agent → window) ─────────────

type WindowHandler = (args: Record<string, unknown>) => unknown | Promise<unknown>;

const _handlers = new Map<string, WindowHandler>();
let _ws: WebSocket | null = null;
let _wsReady = false;
const _wsQueue: string[] = [];  // ws 还没 open 时缓存 outbound

// 重连状态 — P0.1, 指数退避. 后端 restart / 网络抖动后自动恢复, 用户不用 Cmd+R.
let _reconnectAttempt = 0;          // 计数, 退避用. open 成功后清零
let _reconnectTimer: number | null = null;  // setTimeout handle, 关 ws 时取消防双连
let _shouldReconnect = true;        // 用户主动 close window 时不该再重连

function backoffMs(): number {
  // 1s → 2s → 4s → 8s → 16s → 30s (cap)
  const base = Math.min(1000 * 2 ** _reconnectAttempt, 30_000);
  // 加 ±20% jitter 防多 window 同步重连撞后端
  const jitter = base * 0.2 * (Math.random() * 2 - 1);
  return Math.max(500, Math.floor(base + jitter));
}

function scheduleReconnect(): void {
  if (!_shouldReconnect) return;
  if (_reconnectTimer !== null) return;  // 已经排了
  const delay = backoffMs();
  console.info(`[pentaloom] ws reconnect in ${delay}ms (attempt ${_reconnectAttempt + 1})`);
  _reconnectTimer = window.setTimeout(() => {
    _reconnectTimer = null;
    _reconnectAttempt++;
    connectWs();
  }, delay);
}

function wsSend(payload: object): void {
  const s = JSON.stringify(payload);
  if (_wsReady && _ws && _ws.readyState === WebSocket.OPEN) {
    _ws.send(s);
  } else {
    _wsQueue.push(s);
  }
}

function connectWs(): void {
  if (!appName) return;
  if (_ws && (_ws.readyState === WebSocket.OPEN || _ws.readyState === WebSocket.CONNECTING)) {
    return;  // 已在连
  }
  const wsBase = apiBase.replace(/^http/, 'ws');
  const url = `${wsBase}/weaver/apps/${encodeURIComponent(appName)}/window-ws`;
  _ws = new WebSocket(url);
  _ws.addEventListener('open', () => {
    _wsReady = true;
    _reconnectAttempt = 0;  // 连上就清零, 下次断从 1s 重试
    wsSend({ type: 'ready' });
    while (_wsQueue.length > 0) {
      _ws?.send(_wsQueue.shift()!);
    }
    console.info('[pentaloom] ws connected');
  });
  _ws.addEventListener('close', (ev) => {
    _wsReady = false;
    _ws = null;
    console.info(`[pentaloom] ws closed (code=${ev.code} reason=${ev.reason || 'n/a'})`);
    scheduleReconnect();
  });
  _ws.addEventListener('error', (e) => {
    // error 通常紧跟 close, 不在这调 reconnect 避免双安排; close handler 会处理
    console.warn('[pentaloom] ws error', e);
  });
  _ws.addEventListener('message', (ev) => {
    let msg: { type?: string; request_id?: string; invocation_id?: string; args?: Record<string, unknown> };
    try {
      msg = JSON.parse(String(ev.data));
    } catch {
      console.warn('[pentaloom] ws bad json', ev.data);
      return;
    }
    if (msg.type !== 'invoke') return;
    const rid = msg.request_id;
    const iid = msg.invocation_id;
    if (!rid || !iid) return;
    const handler = _handlers.get(iid);
    if (!handler) {
      wsSend({
        type: 'invoke_error',
        request_id: rid,
        error: `no registered handler for invocation '${iid}'`,
      });
      return;
    }
    (async () => {
      try {
        const output = await handler(msg.args ?? {});
        wsSend({
          type: 'invoke_result',
          request_id: rid,
          output: output ?? {},
        });
      } catch (e) {
        wsSend({
          type: 'invoke_error',
          request_id: rid,
          error: e instanceof Error ? e.message : String(e),
        });
      }
    })();
  });
}

// page unload (用户关窗) 时禁止重连 — close 是预期的, 不该再 retry
window.addEventListener('beforeunload', () => {
  _shouldReconnect = false;
  if (_reconnectTimer !== null) {
    window.clearTimeout(_reconnectTimer);
    _reconnectTimer = null;
  }
});

function registerInvocation(invocationId: string, handler: WindowHandler): void {
  if (typeof invocationId !== 'string' || !invocationId) {
    throw new Error('pentaloom.registerInvocation: invocation_id required (string)');
  }
  if (typeof handler !== 'function') {
    throw new Error('pentaloom.registerInvocation: handler must be function');
  }
  _handlers.set(invocationId, handler);
  connectWs();  // 首次注册时建 ws (后续 register 是 noop because 已在连)
}

contextBridge.exposeInMainWorld('pentaloom', {
  appName,
  invokeApp,
  registerInvocation,
});

window.addEventListener('DOMContentLoaded', () => {
  document.documentElement.dataset.pentaloomShell = 'electron';
  document.documentElement.dataset.pentaloomAppName = appName;
});
