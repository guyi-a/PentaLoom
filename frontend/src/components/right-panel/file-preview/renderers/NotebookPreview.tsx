// Jupyter notebook (.ipynb) 预览.
//
// notebook 是 JSON, 含 cells: [{cell_type: 'code'|'markdown'|'raw', source: string|string[], outputs?}]
// 我们简版渲染:
//   - markdown cell: react-markdown 直接渲
//   - code cell: <pre> 朴素显示 (跟 CodePreview 一致)
//   - outputs: 只渲 text/plain stream (不渲 image / html / matplotlib)
// 复杂 notebook (含图表 / 大量 outputs) 走系统 app 看, 第一版不挑战.

import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { TextPreviewResult } from "@/lib/types";

interface Props {
  content: TextPreviewResult;
}

interface NotebookCell {
  cell_type: "code" | "markdown" | "raw";
  source: string | string[];
  outputs?: NotebookOutput[];
}

interface NotebookOutput {
  output_type?: string;
  text?: string | string[];
  data?: Record<string, string | string[]>;
}

function joinSource(s: string | string[]): string {
  return Array.isArray(s) ? s.join("") : s;
}

function joinOutputText(o: NotebookOutput): string {
  if (typeof o.text === "string") return o.text;
  if (Array.isArray(o.text)) return o.text.join("");
  if (o.data && o.data["text/plain"]) {
    const plain = o.data["text/plain"];
    return Array.isArray(plain) ? plain.join("") : plain;
  }
  return "";
}

export function NotebookPreview({ content }: Props) {
  const cells = useMemo<NotebookCell[] | null>(() => {
    try {
      const json = JSON.parse(content.content);
      return Array.isArray(json?.cells) ? json.cells : null;
    } catch {
      return null;
    }
  }, [content.content]);

  if (cells === null) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <div className="text-[12px] text-[color:var(--color-error)]">
          Notebook 解析失败 — 可能文件被截断或不是合法 ipynb JSON
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="scrollbar-hidden min-h-0 flex-1 overflow-auto px-4 py-3">
        <ol className="space-y-3">
          {cells.map((cell, i) => (
            <CellBlock key={i} cell={cell} index={i} />
          ))}
        </ol>
      </div>
      {content.truncated && (
        <div className="shrink-0 border-t border-[color:var(--color-line-soft)] px-3 py-1.5 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          truncated at 512 KB · file size {(content.size / 1024).toFixed(1)} KB
        </div>
      )}
    </div>
  );
}

function CellBlock({ cell, index }: { cell: NotebookCell; index: number }) {
  const source = joinSource(cell.source);

  if (cell.cell_type === "markdown") {
    return (
      <li className="rounded-[4px] border border-[color:var(--color-line-soft)] px-3 py-2">
        <div className="mb-1 font-mono text-[9.5px] uppercase text-[color:var(--color-ink-dim)]">
          [{index + 1}] markdown
        </div>
        <article className="prose-loom max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
        </article>
      </li>
    );
  }

  if (cell.cell_type === "code") {
    const outputText = (cell.outputs ?? [])
      .map(joinOutputText)
      .filter(Boolean)
      .join("\n");
    return (
      <li className="rounded-[4px] border border-[color:var(--color-line-soft)]">
        <div className="px-3 pt-2 font-mono text-[9.5px] uppercase text-[color:var(--color-ink-dim)]">
          [{index + 1}] code
        </div>
        <pre className="whitespace-pre-wrap break-words px-3 pb-2 pt-1 font-mono text-[11.5px] leading-[1.5] text-[color:var(--color-paper)]">
          {source}
        </pre>
        {outputText && (
          <div className="border-t border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-deep)] px-3 py-2">
            <div className="mb-1 font-mono text-[9.5px] uppercase text-[color:var(--color-ink-dim)]">
              output
            </div>
            <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-[1.5] text-[color:var(--color-paper-dim)]">
              {outputText}
            </pre>
          </div>
        )}
      </li>
    );
  }

  // raw / 其他 — 朴素渲
  return (
    <li className="rounded-[4px] border border-[color:var(--color-line-soft)] px-3 py-2">
      <div className="mb-1 font-mono text-[9.5px] uppercase text-[color:var(--color-ink-dim)]">
        [{index + 1}] {cell.cell_type}
      </div>
      <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] text-[color:var(--color-paper-dim)]">
        {source}
      </pre>
    </li>
  );
}
