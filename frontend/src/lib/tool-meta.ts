// 工具显示元数据 + 路径相关辅助.
// ToolRow / ContextSection / 老 FrameBlock helpers 共用同一份, 改名/加图标只动这里.

import {
  Code2,
  File as FileIcon,
  FileCode,
  FileImage,
  FilePlus2,
  FileText,
  FolderOpen,
  Globe,
  ListChecks,
  Package,
  Pencil,
  Play,
  Search,
  ShieldCheck,
  Sparkles,
  Terminal,
  Type,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import {
  BASH_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
  INSTALL_FONT_TOOL_NAME,
  INSTALL_LIBS_TOOL_NAME,
  RUN_SCRIPT_TOOL_NAME,
  WORKSPACE_PERMISSION_TOOL_NAME,
} from "./types";

// 显式映射 — 名字硬编码不优雅但比模糊 includes 准, 工具数量有限.
// 没命中的走通用 Wrench. 兜底用 Hammer 还是 Wrench 区分有意义: Wrench 是兜底,
// 出现就说明这里少加了一个 case, 给开发者一个"哪些工具图标还没设"的信号.
const TOOL_ICON_MAP: Record<string, LucideIcon> = {
  [BASH_TOOL_NAME]: Terminal,
  Read: FileText,
  Write: FilePlus2,
  Edit: Pencil,
  Glob: Search,
  Grep: Search,
  Task: Play,
  TodoWrite: ListChecks,
  Skill: Sparkles,
  WebFetch: Globe,
  WebSearch: Globe,
  [WORKSPACE_PERMISSION_TOOL_NAME]: FolderOpen,
  [INSTALL_LIBS_TOOL_NAME]: Package,
  [RUN_SCRIPT_TOOL_NAME]: Code2,
  [FILE_VERIFY_TOOL_NAME]: ShieldCheck,
  [INSTALL_FONT_TOOL_NAME]: Type,
  // pentaloom_files 下的 file_read / file_write 等
  "mcp__pentaloom_files__file_read": FileText,
};

export function toolIcon(name: string): LucideIcon {
  const hit = TOOL_ICON_MAP[name];
  if (hit) return hit;
  // file_xxx → 文件 icon; 其它走兜底
  if (name.includes("file_read") || name.includes("file_write")) return FileText;
  if (name.toLowerCase().includes("bash")) return Terminal;
  return Wrench;
}

export function friendlyToolName(name: string): string {
  // mcp__pentaloom__request_workspace_dir → "request workspace dir"
  if (name.startsWith("mcp__")) {
    return name.split("__").slice(2).join("·").replace(/_/g, " ");
  }
  return name;
}

export function threadColorForTool(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("bash")) return "var(--color-thread-computer)";
  if (n === "task" || n.startsWith("task")) return "var(--color-thread-app)";
  if (n.includes("fetch") || n.includes("search") || n.includes("web"))
    return "var(--color-thread-search)";
  if (n.includes("browser")) return "var(--color-thread-browser)";
  if (
    n.includes("read") ||
    n.includes("write") ||
    n.includes("edit") ||
    n.includes("glob") ||
    n.includes("grep")
  )
    return "var(--color-thread-file)";
  return "var(--color-line-strong)";
}

// 一行摘要 — chip 折叠态显示在工具名右边. 留白时折叠态干净.
export function oneLineSummary(
  name: string,
  input: Record<string, unknown>,
): string {
  const n = name.toLowerCase();
  if (n.includes("read") && typeof input.file_path === "string")
    return input.file_path;
  if ((n.includes("write") || n.includes("edit")) && typeof input.file_path === "string")
    return input.file_path;
  if (n.includes("bash") && typeof input.command === "string")
    return truncate(input.command, 100);
  if (n.includes("glob") && typeof input.pattern === "string") return input.pattern;
  if (n.includes("grep") && typeof input.pattern === "string") return input.pattern;
  if (name === "Task" && typeof input.description === "string")
    return input.description;
  if (name === "TodoWrite") {
    const todos = input.todos;
    if (Array.isArray(todos)) return `${todos.length} item${todos.length === 1 ? "" : "s"}`;
  }
  if (name === "Skill" && typeof input.skill === "string") return input.skill;
  if (name === RUN_SCRIPT_TOOL_NAME && typeof input.script_path === "string")
    return input.script_path;
  if (name === INSTALL_LIBS_TOOL_NAME && Array.isArray(input.libs))
    return input.libs.map((x) => String(x)).join(" ");
  if (typeof input.path === "string") return input.path;
  if (typeof input.url === "string") return input.url;
  return "";
}

export function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + "…";
}

// ──── 文件路径相关 ──────────────────────────────────────────

// 后端 ChatPage 看到的 tool_use input 里, 这些 key 的值如果是字符串 + 绝对路径,
// 就被 ContextSection 收集进"本会话访问过的文件"列表.
export const PATH_KEYS: readonly string[] = [
  "path",
  "file_path",
  "script_path",
  "output_path",
  "notebook_path",
];

export function basename(p: string): string {
  const cleaned = p.endsWith("/") ? p.slice(0, -1) : p;
  const idx = cleaned.lastIndexOf("/");
  return idx === -1 ? cleaned : cleaned.slice(idx + 1);
}

export function extOf(p: string): string {
  const base = basename(p);
  const idx = base.lastIndexOf(".");
  if (idx <= 0) return ""; // 隐藏文件 .env 视作无扩展
  return base.slice(idx + 1).toLowerCase();
}

// ext → lucide icon. 仅给 ContextSection 用; ToolRow 走 toolIcon (按 tool name).
export function iconForExt(ext: string): LucideIcon {
  if (["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt", "md", "rtf"].includes(ext))
    return FileText;
  if (
    [
      "py", "ts", "tsx", "js", "jsx", "go", "java", "rs", "c", "cpp", "h", "hpp",
      "rb", "php", "swift", "kt", "scala", "sh", "bash", "zsh", "fish",
      "json", "yaml", "yml", "toml", "xml", "html", "css", "scss", "sql",
    ].includes(ext)
  )
    return FileCode;
  if (["png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "tiff"].includes(ext))
    return FileImage;
  return FileIcon;
}
