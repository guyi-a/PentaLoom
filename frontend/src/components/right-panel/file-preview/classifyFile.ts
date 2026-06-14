// File preview kind 分类 — 按 ext + basename 兜底.
//
// 设计:
//   - 纯函数, 单测友好 (TBD: 进 git 单测在 GPT review 跟项目约定权衡后跳过)
//   - 第一 PR 5 种 kind: code / markdown / image / pdf / unsupported
//   - 第二 PR 加: media (video/audio) / table (csv) / notebook (ipynb) / docx / xlsx / pptx
//   - basename 兜底无后缀文件 (Dockerfile / .env / .gitignore 等)
//
// 跟 krow 的差别:
//   - 不引 shiki 的 bundledLanguagesInfo 当 lookup (shiki bundle 大), 用手维列表
//   - 加新语言时改 CODE_EXTS 即可

export type FilePreviewKind =
  | "code"
  | "markdown"
  | "image"
  | "pdf"
  | "video"
  | "audio"
  | "table"
  | "notebook"
  | "docx"
  | "xlsx"
  | "pptx"
  | "archive"
  | "database"
  | "unsupported";

const MARKDOWN_EXTS = new Set(["md", "markdown", "mdx"]);

const IMAGE_EXTS = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico",
]);

const VIDEO_EXTS = new Set(["mp4", "webm", "mov", "m4v", "mkv"]);
const AUDIO_EXTS = new Set(["mp3", "wav", "ogg", "m4a", "aac", "flac", "opus"]);

const TABLE_EXTS = new Set(["csv", "tsv"]);
const NOTEBOOK_EXTS = new Set(["ipynb"]);
const XLSX_EXTS = new Set(["xlsx", "xlsm"]);
// archive — 第一版只支持 zip. tar/gz/7z 后续 PR 加.
const ARCHIVE_EXTS = new Set(["zip"]);
// database — sqlite 三种常见扩展名 (.db / .sqlite / .sqlite3).
const DATABASE_EXTS = new Set(["db", "sqlite", "sqlite3"]);

// 主流编程语言 + 配置 + 文本. shiki 之外的 ext 命中也归 code, 让 CodePreview 走兜底高亮.
const CODE_EXTS = new Set([
  // 编程语言
  "ts", "tsx", "js", "jsx", "mjs", "cjs",
  "py", "pyi", "rb", "go", "rs", "java", "kt", "scala", "clj",
  "c", "cc", "cpp", "h", "hpp", "hh", "cs", "swift",
  "php", "lua", "r", "dart", "ex", "exs", "erl", "elm", "ml", "mli",
  "sh", "bash", "zsh", "fish", "ps1",
  // markup / config / data
  "json", "jsonc", "json5", "yaml", "yml", "toml", "xml", "html", "htm",
  "css", "scss", "sass", "less",
  "sql", "graphql", "gql", "proto",
  // text
  "txt", "log", "diff", "patch",
  "env", "ini", "cfg", "conf", "properties", "dockerfile", "makefile",
  "gitignore", "gitattributes", "lock",
]);

// 无 ext 的特殊文件名 (basename 兜底). 全小写比较.
const SPECIAL_BASENAMES = new Set([
  "dockerfile", "makefile", "rakefile", "gemfile", "procfile",
  ".env", ".gitignore", ".gitattributes", ".dockerignore",
  ".npmrc", ".prettierrc", ".eslintrc", ".babelrc",
]);

export function classifyFile(ext: string, basename?: string): FilePreviewKind {
  const e = ext.toLowerCase();

  if (e === "pdf") return "pdf";
  if (MARKDOWN_EXTS.has(e)) return "markdown";
  if (IMAGE_EXTS.has(e)) return "image";
  if (VIDEO_EXTS.has(e)) return "video";
  if (AUDIO_EXTS.has(e)) return "audio";
  if (NOTEBOOK_EXTS.has(e)) return "notebook";
  if (TABLE_EXTS.has(e)) return "table";
  if (e === "docx") return "docx";
  if (e === "pptx") return "pptx";
  if (XLSX_EXTS.has(e)) return "xlsx";
  if (ARCHIVE_EXTS.has(e)) return "archive";
  if (DATABASE_EXTS.has(e)) return "database";
  if (CODE_EXTS.has(e)) return "code";

  // basename 兜底 — Dockerfile / .env 这种无 ext 文件
  if (basename) {
    const b = basename.toLowerCase();
    if (SPECIAL_BASENAMES.has(b)) return "code";
    // 多段名也试一下: e.g., "Dockerfile.prod" → 命中 dockerfile
    const head = b.split(".")[0];
    if (head && SPECIAL_BASENAMES.has(head)) return "code";
  }

  return "unsupported";
}
