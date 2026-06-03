/**
 * PentaLoom Electron Main 进程入口.
 *
 * 职责:
 *  1. 起 BrowserWindow loadURL Vite 5273 (前端 dev server)
 *  2. preload 注入空 apiBase (dev 走 Vite proxy /api → agent 8090)
 *  3. macOS hiddenInset titlebar + dock icon (LoomMark)
 *  4. window-all-closed → quit (跟 Wolfpack 同款, 不沿 macOS 默认"留 dock 不退" 习惯,
 *     因为 dev 阶段就是开窗壳, 关窗 = 退应用)
 *  5. (Phase C-0) IPC pentaloom:open-app-window — Renderer 调起新 BrowserWindow 加载
 *     weaver app window endpoint, agent 织出的 app 通过 sidebar AppDetailModal 触发.
 *
 * **不做** (留给后续 prod milestone):
 *  - spawn Python 后端 (用户自己 ./start-dev.sh 起 agent)
 *  - 端口探测 / 健康检查
 *  - PyInstaller binary 打包
 *  - electron-builder .app / .dmg
 *  - 自动更新 / sentry
 *
 * 跑法:
 *   pnpm install
 *   pnpm start              # tsc + electron .  (前提: agent + frontend dev 已起)
 *   # 或: 项目根 ./start-dev.sh --electron 一键起所有
 */

import { app, BrowserWindow, ipcMain } from 'electron';
import path from 'node:path';

const DEV_RENDERER_URL = 'http://localhost:5273';
const AGENT_BASE = 'http://localhost:8090';  // weaver app window HTML 由后端 serve

app.setName('PentaLoom');

if (process.platform === 'darwin') {
  const iconPath = path.join(__dirname, '../../assets/icon.png');
  // dock.setIcon 失败 (asset 缺) 不该挂主进程
  try {
    app.dock?.setIcon(iconPath);
  } catch {
    /* dev 占位 icon 缺也无所谓 */
  }
}

let mainWindow: BrowserWindow | null = null;

// 同名 weaver app 同时只允许一个 window — 避免用户狂点开一堆同样的 modal.
// key = app name, value = BrowserWindow. window close 时清理.
const appWindows = new Map<string, BrowserWindow>();

function createWindow(): void {
  const preloadPath = path.join(__dirname, '../preload/preload.js');

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 28, y: 24 },
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: preloadPath,
      // dev 模式 apiBase 留空, Renderer 用 /api 走 Vite proxy 到 agent 8090.
      // prod 模式以后扩 spawn binary + 动态端口时, 这里改注入实际 apiBase.
      additionalArguments: ['--pentaloom-api-base='],
      devTools: true,
    },
  });

  mainWindow.loadURL(DEV_RENDERER_URL);
  mainWindow.webContents.openDevTools();

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/**
 * IPC: 打开一个 weaver app 的 window.
 *
 * payload: { name: string, entry?: string, title?: string, width?: number, height?: number }
 *  - name (必填): app 名, e.g., 'md-stats'
 *  - entry (可选): window 入口 HTML 相对路径, 不传后端会从 app.json 第一个 window 取
 *  - title/width/height: 不传走 app.json windows[].title/width/height, 再退默认
 *
 * Phase C-0 不注入 invokeApp 之类的 preload, 仅 loadURL 看到 HTML 内容. preload bridge
 * 留到 C-1 加 (开放给 window 内部调后端 invocations).
 */
ipcMain.handle(
  'pentaloom:open-app-window',
  async (
    _event,
    payload: { name?: string; entry?: string; title?: string; width?: number; height?: number },
  ) => {
    const name = String(payload?.name ?? '').trim();
    if (!name) {
      throw new Error('open-app-window: name required');
    }

    // 已开则聚焦 + 返同 id, 不开新窗 (防滥开)
    const existing = appWindows.get(name);
    if (existing && !existing.isDestroyed()) {
      existing.focus();
      return { name, reused: true };
    }

    // 直接 loadURL 到 agent (不经 vite proxy, 所以 URL 不含 /api 前缀).
    // 后端 router 路径就是 /weaver/apps/{name}/window.
    const url = new URL(`${AGENT_BASE}/weaver/apps/${encodeURIComponent(name)}/window`);
    if (payload.entry) url.searchParams.set('entry', payload.entry);

    const win = new BrowserWindow({
      width: payload.width ?? 720,
      height: payload.height ?? 520,
      title: payload.title ?? `${name} · PentaLoom app`,
      titleBarStyle: process.platform === 'darwin' ? 'default' : 'default',
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        // C-1: 挂 preload-app-window 注入 window.pentaloom.invokeApp.
        // additionalArguments 传 app name + apiBase, preload 闭包死 name 防越权.
        preload: path.join(__dirname, '../preload/preload-app-window.js'),
        additionalArguments: [
          `--pentaloom-app-name=${name}`,
          `--pentaloom-api-base=${AGENT_BASE}`,
        ],
        sandbox: true,
        devTools: true,
      },
    });

    win.loadURL(url.toString());
    // 不自动开 DevTools — 用户开 app 不应该被 DevTools 窗口打扰. 需要调试按 Cmd+Option+I.

    win.on('closed', () => {
      appWindows.delete(name);
    });

    appWindows.set(name, win);
    return { name, reused: false };
  },
);

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  app.quit();
});
