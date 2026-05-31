/**
 * PentaLoom preload 脚本.
 *
 * 在 Renderer 拿到 window 对象之前跑, 用 contextBridge 把 Main 进程发现的
 * 后端 apiBase 暴露到 window.__PENTALOOM__.apiBase. 前端 lib/api.ts 可以
 * 优先读它 (现在 frontend 还没接, 走 Vite proxy /api, 这值留空).
 *
 * apiBase 通过 BrowserWindow webPreferences.additionalArguments 传进来,
 * 形如 `--pentaloom-api-base=http://127.0.0.1:8090`. dev 模式传空字符串.
 * prod 模式以后 spawn 后端 + 探到动态端口后注入实际地址.
 */

import { contextBridge } from 'electron';

const PREFIX = '--pentaloom-api-base=';

function readApiBase(): string {
  const arg = process.argv.find((a) => a.startsWith(PREFIX));
  return arg ? arg.slice(PREFIX.length) : '';
}

contextBridge.exposeInMainWorld('__PENTALOOM__', {
  apiBase: readApiBase(),
});

window.addEventListener('DOMContentLoaded', () => {
  document.documentElement.dataset.pentaloomShell = 'electron';
});
