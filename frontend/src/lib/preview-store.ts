// Right-panel file preview 状态管理. 跨组件共享, 用 zustand (项目已装).
//
// Context chip 点击 → openPreview(file). previewFile 切到 'preview' 模式, RightPanel
// 让位 (跟 krow 同款 mode 切换), preview 列接管. previewWidth 独立持久化, 拖动调.

import { create } from "zustand";

export interface PreviewFile {
  path: string;        // 绝对路径 — 后端鉴权 key
  name: string;        // basename, header 显示用
  // 可选: 显示用的相对路径 (例如 sandbox 子树内). 不影响行为.
  relativePath?: string;
}

// preview 列宽度范围. 跟 krow 类似 (360-760, default 520) 但稍宽上限以适应大屏.
export const PREVIEW_WIDTH_MIN = 360;
export const PREVIEW_WIDTH_MAX = 800;
export const PREVIEW_WIDTH_DEFAULT = 520;
const PREVIEW_WIDTH_LS_KEY = "pentaloom:preview-width";

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function loadInitialWidth(): number {
  if (typeof window === "undefined") return PREVIEW_WIDTH_DEFAULT;
  const saved = Number(window.localStorage.getItem(PREVIEW_WIDTH_LS_KEY));
  return Number.isFinite(saved) && saved > 0
    ? clamp(saved, PREVIEW_WIDTH_MIN, PREVIEW_WIDTH_MAX)
    : PREVIEW_WIDTH_DEFAULT;
}

interface PreviewState {
  previewFile: PreviewFile | null;
  previewWidth: number;
  openPreview: (file: PreviewFile) => void;
  closePreview: () => void;
  setPreviewWidth: (width: number) => void;
}

export const usePreviewStore = create<PreviewState>((set) => ({
  previewFile: null,
  previewWidth: loadInitialWidth(),
  openPreview: (file) => set({ previewFile: file }),
  closePreview: () => set({ previewFile: null }),
  setPreviewWidth: (width) => {
    const w = clamp(Math.round(width), PREVIEW_WIDTH_MIN, PREVIEW_WIDTH_MAX);
    set({ previewWidth: w });
    try {
      window.localStorage.setItem(PREVIEW_WIDTH_LS_KEY, String(w));
    } catch {
      /* localStorage 不可用就算了 */
    }
  },
}));
