// Shiki 代码高亮 — CodePreview 用. 精简版 (light only, 跟 PentaLoom 主题一致).
//
// 设计:
//   - lazy createHighlighter (~首次调用时初始化, 之后复用)
//   - 按需 loadLanguage (不一开始就把所有语言塞 bundle, 第一个文件预览拉对应 lang)
//   - 文件路径 → BundledLanguage 解析 (含 ext + 特殊 basename)
//
// 跟 markdown / chat stream 用的 highlight 不冲突 (那俩第一版不引 shiki).

import {
  bundledLanguagesInfo,
  createHighlighter,
  type BundledLanguage,
  type BundledTheme,
  type Highlighter,
} from "shiki";

// 用 GitHub Light — 跟 PentaLoom 雾白底协调, 衬线感强的代码高亮里它最干净.
export const SHIKI_THEME: BundledTheme = "github-light";

// 构建 lookup: shiki id + aliases → 真正的 BundledLanguage id
const langLookup: Record<string, BundledLanguage> = {};
for (const lang of bundledLanguagesInfo) {
  langLookup[lang.id] = lang.id as BundledLanguage;
  for (const alias of lang.aliases ?? []) {
    langLookup[alias] = lang.id as BundledLanguage;
  }
}

// shiki 没覆盖的常用扩展
const extraExtMap: Record<string, BundledLanguage> = {
  mjs: "javascript",
  cjs: "javascript",
  mts: "typescript",
  cts: "typescript",
  htm: "html",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
};

// shiki 没 plaintext, 用 bash 当兜底 (最朴素的 mono 高亮)
const FALLBACK_LANG: BundledLanguage = "bash";

export function resolveLanguage(filePathOrName: string): BundledLanguage {
  if (!filePathOrName) return FALLBACK_LANG;
  const fileName = filePathOrName.split(/[/\\]/).pop() ?? "";
  if (!fileName) return FALLBACK_LANG;
  const lower = fileName.toLowerCase();

  // 无 ext 特殊 basename
  if (lower === "dockerfile" || lower.startsWith("dockerfile.")) return "dockerfile";
  if (lower === "makefile") return "makefile";

  const ext = fileName.includes(".") ? fileName.split(".").pop()?.toLowerCase() : "";
  if (!ext) return FALLBACK_LANG;
  if (ext in langLookup) return langLookup[ext];
  if (ext in extraExtMap) return extraExtMap[ext];
  return FALLBACK_LANG;
}

let highlighterPromise: Promise<Highlighter> | null = null;

async function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: [SHIKI_THEME],
      langs: [], // 按需加载
    });
  }
  return highlighterPromise;
}

export async function highlightCode(
  code: string,
  lang: BundledLanguage,
  options?: { showLineNumbers?: boolean },
): Promise<string> {
  const hl = await getHighlighter();
  if (!hl.getLoadedLanguages().includes(lang)) {
    try {
      await hl.loadLanguage(lang);
    } catch {
      // 语言加载失败 fall through, 用 fallback
    }
  }
  const actualLang = hl.getLoadedLanguages().includes(lang) ? lang : FALLBACK_LANG;
  const transformers = options?.showLineNumbers ? [lineNumberTransformer()] : [];
  return hl.codeToHtml(code, {
    lang: actualLang,
    theme: SHIKI_THEME,
    transformers,
  });
}

function lineNumberTransformer() {
  return {
    name: "pl-line-numbers",
    line(node: { children: unknown[] }, line: number) {
      node.children.unshift({
        type: "element",
        tagName: "span",
        properties: {
          className: ["pl-line-no"],
        },
        children: [{ type: "text", value: String(line) }],
      });
    },
  };
}
