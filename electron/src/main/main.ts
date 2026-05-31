/**
 * PentaLoom Electron Main 进程入口 (M15 dev-only shell).
 *
 * 职责:
 *  1. 起 BrowserWindow loadURL Vite 5273 (前端 dev server)
 *  2. preload 注入空 apiBase (dev 走 Vite proxy /api → agent 8090)
 *  3. macOS hiddenInset titlebar + dock icon (LoomMark)
 *  4. window-all-closed → quit (跟 Wolfpack 同款, 不沿 macOS 默认"留 dock 不退" 习惯,
 *     因为 dev 阶段就是开窗壳, 关窗 = 退应用)
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

import { app, BrowserWindow } from 'electron';
import path from 'node:path';

const DEV_RENDERER_URL = 'http://localhost:5273';

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
