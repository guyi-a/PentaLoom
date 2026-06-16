// 简单 utility — 验证多文件 ts (非 tsx) 也能解析.
export function fmtKB(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}
