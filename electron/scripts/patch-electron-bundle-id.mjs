#!/usr/bin/env node
// 把本项目 node_modules 里 electron 二进制的 CFBundleIdentifier 改成
// com.pentaloom.dev.electron，避免和系统上其他 dev electron (如 krow-app)
// 共用默认 com.github.Electron 导致 macOS LaunchServices 互相串
// (现象: 启动别人的 app 时弹出 PentaLoom 的 default_app 欢迎页)。
// 仅 macOS 生效；幂等；prebuilt electron 是 adhoc + Info.plist not-bound,
// 改完 plist 不重签也能跑,这里仍重签一次以便 LS 立即重新索引。

import { execSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

if (process.platform !== 'darwin') process.exit(0);

const NEW_ID = 'com.pentaloom.dev.electron';
const LSREGISTER =
  '/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister';

const require = createRequire(import.meta.url);
let electronDir;
try {
  electronDir = path.dirname(require.resolve('electron/package.json'));
} catch {
  console.warn('[patch-electron-bundle-id] electron not installed, skipping');
  process.exit(0);
}

const appPath = path.join(electronDir, 'dist', 'Electron.app');
const plistPath = path.join(appPath, 'Contents', 'Info.plist');
if (!existsSync(plistPath)) {
  console.warn(`[patch-electron-bundle-id] ${plistPath} not found, skipping`);
  process.exit(0);
}

const current = execSync(
  `/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "${plistPath}"`,
  { encoding: 'utf8' },
).trim();

if (current === NEW_ID) {
  console.log(`[patch-electron-bundle-id] already ${NEW_ID}, nothing to do`);
  process.exit(0);
}

execSync(
  `/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ${NEW_ID}" "${plistPath}"`,
  { stdio: 'inherit' },
);

try {
  execSync(`codesign --force --deep --sign - "${appPath}"`, { stdio: 'pipe' });
} catch (err) {
  console.warn('[patch-electron-bundle-id] adhoc resign skipped:', err.message);
}

try {
  execSync(`"${LSREGISTER}" "${appPath}"`, { stdio: 'pipe' });
} catch {
  /* lsregister failure is non-fatal — next app launch will reindex anyway */
}

console.log(`[patch-electron-bundle-id] patched ${current} → ${NEW_ID}`);
