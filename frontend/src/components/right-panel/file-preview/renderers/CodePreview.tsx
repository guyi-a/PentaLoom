// 代码 / 文本预览 — shiki 服务端预渲染高亮 + 行号.
//
// shiki 按需加载语言 — 第一次预览 .py 文件才拉 python grammar, .ts 拉 typescript.
// theme: github-light, 跟 PentaLoom 雾白底协调.

import { useEffect, useState } from "react";

import type { TextPreviewResult } from "@/lib/types";
import { highlightCode, resolveLanguage } from "@/lib/shiki";

interface Props {
  content: TextPreviewResult;
  fileName: string;  // 决定 shiki language
}

export function CodePreview({ content, fileName }: Props) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setHtml(null);
    const lang = resolveLanguage(fileName);
    highlightCode(content.content, lang, { showLineNumbers: true })
      .then((h) => {
        if (!cancelled) setHtml(h);
      })
      .catch(() => {
        // 高亮失败 fall back to plain — 不挂掉整个 preview
        if (!cancelled) setHtml(null);
      });
    return () => {
      cancelled = true;
    };
  }, [content.content, fileName]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="scrollbar-hidden min-h-0 flex-1 overflow-auto">
        {html ? (
          <div
            className="pl-shiki-host"
            dangerouslySetInnerHTML={{ __html: html }}
          />
        ) : (
          // shiki 加载期间走 plain text 兜底, 不闪 spinner (大多数 ms 级)
          <pre className="whitespace-pre p-3 font-mono text-[11.5px] leading-[1.55] text-[color:var(--color-paper)]">
            {content.content}
          </pre>
        )}
      </div>
      {content.truncated && (
        <div className="shrink-0 border-t border-[color:var(--color-line-soft)] px-3 py-1.5 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          truncated at 512 KB · file size {(content.size / 1024).toFixed(1)} KB
        </div>
      )}
    </div>
  );
}
