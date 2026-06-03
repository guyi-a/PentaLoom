/**
 * PentaLoom preload 脚本 (主 shell window 用).
 *
 * 用 contextBridge 把以下 API 暴露到 window.__PENTALOOM__:
 *   - apiBase: 后端地址 (dev 走 Vite proxy 留空, prod 注入动态端口)
 *   - openAppWindow(payload): 开 weaver app 的 BrowserWindow (Phase C-0)
 *
 * apiBase 通过 BrowserWindow webPreferences.additionalArguments 传进来,
 * 形如 `--pentaloom-api-base=http://127.0.0.1:8090`. dev 模式传空字符串.
 */

import { contextBridge, ipcRenderer } from 'electron';

const PREFIX = '--pentaloom-api-base=';

function readApiBase(): string {
  const arg = process.argv.find((a) => a.startsWith(PREFIX));
  return arg ? arg.slice(PREFIX.length) : '';
}

interface OpenAppWindowPayload {
  name: string;
  entry?: string;
  title?: string;
  width?: number;
  height?: number;
}

contextBridge.exposeInMainWorld('__PENTALOOM__', {
  apiBase: readApiBase(),
  // Phase C-0: 开 weaver app 的 BrowserWindow. 返 {name, reused: bool}.
  // reused=true 表示同名 window 已开, 这次只是 focus 它, 没开新窗.
  openAppWindow(payload: OpenAppWindowPayload): Promise<{ name: string; reused: boolean }> {
    return ipcRenderer.invoke('pentaloom:open-app-window', payload);
  },
});

window.addEventListener('DOMContentLoaded', () => {
  document.documentElement.dataset.pentaloomShell = 'electron';
});
