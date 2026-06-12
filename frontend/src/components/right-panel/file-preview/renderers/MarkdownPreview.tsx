// Markdown 预览. react-markdown + remark-gfm + PentaLoom 自家 .prose-loom 样式.
//
// .prose-loom 在 index.css 定义 — Atelier Nordic 主题的 markdown 排版 (Fraunces
// 标题 / 衬线 blockquote / paper 色文字 / mono code block + bg-deep). 不要去用
// @tailwindcss/typography 默认 prose, PentaLoom 颜色 token 不是浏览器默认黑.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { TextPreviewResult } from "@/lib/types";

interface Props {
  content: TextPreviewResult;
}

export function MarkdownPreview({ content }: Props) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="scrollbar-hidden min-h-0 flex-1 overflow-auto px-5 py-4">
        <article className="prose-loom max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {content.content}
          </ReactMarkdown>
        </article>
      </div>
      {content.truncated && (
        <div className="shrink-0 border-t border-[color:var(--color-line-soft)] px-3 py-1.5 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          truncated at 512 KB · file size {(content.size / 1024).toFixed(1)} KB
        </div>
      )}
    </div>
  );
}
