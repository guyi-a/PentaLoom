/**
 * PentaLoom preload 脚本 (主 shell window 用).
 *
 * 用 contextBridge 把以下 API 暴露到 window.__PENTALOOM__:
 *   - apiBase: 后端地址 (dev 走 Vite proxy 留空, prod 注入动态端口)
 *
 * apiBase 通过 BrowserWindow webPreferences.additionalArguments 传进来,
 * 形如 `--pentaloom-api-base=http://127.0.0.1:8090`. dev 模式传空字符串.
 *
 * M21: invocable app window 不再走 Electron BrowserWindow IPC, 改成前端 fetch
 * POST /weaver/apps/<n>/window/open → 后端 → loom Unix socket → loomer 子进程.
 * 因此本 preload 不再暴露 openAppWindow.
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
