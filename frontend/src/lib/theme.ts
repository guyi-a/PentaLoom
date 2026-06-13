/** 主题切换工具 — light / dark / system 三档.
 *
 * applyTheme:  给 <html> 加/删 .dark class + 写 localStorage (防闪烁).
 * getSystemTheme:  读 OS 当前偏好.
 * watchSystemTheme:  监听 OS 偏好变化, 返回 unsubscribe.
 */

export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "pentaloom:theme";

/** 读 OS 当前偏好. */
export function getSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/** 把 Theme 值 resolve 成实际的 light / dark. */
export function resolveTheme(t: Theme): ResolvedTheme {
  return t === "system" ? getSystemTheme() : t;
}

/** 给 <html> 加/删 .dark class + 写 localStorage. */
export function applyTheme(t: Theme): void {
  const resolved = resolveTheme(t);
  const root = document.documentElement;
  if (resolved === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
  // localStorage 跟 settings.json 双写, 防 hydrate 前闪烁
  try {
    window.localStorage.setItem(STORAGE_KEY, resolved);
  } catch {
    /* 不可用就算了 */
  }
}

/** 监听 OS 偏好变化, 当 theme=system 时自动切. 返回 unsubscribe. */
export function watchSystemTheme(onChange: (resolved: ResolvedTheme) => void): () => void {
  const mql = window.matchMedia("(prefers-color-scheme: dark)");
  function handler(e: MediaQueryListEvent) {
    onChange(e.matches ? "dark" : "light");
  }
  mql.addEventListener("change", handler);
  return () => mql.removeEventListener("change", handler);
}

/** 启动时读 localStorage 恢复主题 (在 React hydrate 之前调用, 防闪烁). */
export function initThemeFromStorage(): void {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "dark") {
      document.documentElement.classList.add("dark");
    }
  } catch {
    /* 不可用就算了 */
  }
}
