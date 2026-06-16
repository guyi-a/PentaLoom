// CSV / TSV 预览 — 简版 HTML table.
//
// 第一版用最朴素 split: 不处理引号转义 / 多行 cell. 复杂 csv 走 `unsupported` + 系统 app 兜底.
// TODO: 接 papaparse 之类正经 csv 解析.

import { useMemo } from "react";

import type { TextPreviewResult } from "@/lib/types";

interface Props {
  content: TextPreviewResult;
  ext: string;  // csv / tsv 决定分隔符
}

const MAX_ROWS = 500;
const MAX_COLS = 50;

function parse(text: string, sep: string): { headers: string[]; rows: string[][]; tooManyRows: boolean; tooManyCols: boolean } {
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length === 0) {
    return { headers: [], rows: [], tooManyRows: false, tooManyCols: false };
  }
  const headers = lines[0].split(sep);
  const tooManyCols = headers.length > MAX_COLS;
  const cappedHeaders = headers.slice(0, MAX_COLS);
  const dataLines = lines.slice(1, MAX_ROWS + 1);
  const tooManyRows = lines.length - 1 > MAX_ROWS;
  const rows = dataLines.map((l) => l.split(sep).slice(0, MAX_COLS));
  return { headers: cappedHeaders, rows, tooManyRows, tooManyCols };
}

export function TablePreview({ content, ext }: Props) {
  const sep = ext === "tsv" ? "\t" : ",";
  const { headers, rows, tooManyRows, tooManyCols } = useMemo(
    () => parse(content.content, sep),
    [content.content, sep],
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="scrollbar-hidden min-h-0 flex-1 overflow-auto">
        <table className="border-collapse font-mono text-[11px]">
          <thead>
            <tr>
              <th className="sticky top-0 left-0 z-20 min-w-[40px] bg-[color:var(--color-bg-soft)] border-r border-b border-[color:var(--color-line-soft)] px-1 py-1 text-[10px] font-normal text-[color:var(--color-ink-dim)] select-none" />
              {headers.map((h, i) => (
                <th
                  key={i}
                  className="sticky top-0 z-10 min-w-[100px] bg-[color:var(--color-bg-soft)] border-r border-b border-[color:var(--color-line-soft)] px-2 py-1 text-left font-medium text-[color:var(--color-paper)]"
                >
                  {h || `col_${i + 1}`}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                <td className="sticky left-0 z-10 bg-[color:var(--color-bg-soft)] border-r border-b border-[color:var(--color-line-soft)] px-1 py-1 text-[10px] text-[color:var(--color-ink-dim)] text-center select-none">
                  {r + 1}
                </td>
                {headers.map((_, c) => (
                  <td
                    key={c}
                    className="border-r border-b border-[color:var(--color-line-soft)] px-2 py-1 text-[color:var(--color-paper-dim)] whitespace-nowrap"
                    title={row[c] ?? ""}
                  >
                    {row[c] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(tooManyRows || tooManyCols || content.truncated) && (
        <div className="shrink-0 border-t border-[color:var(--color-line-soft)] px-3 py-1.5 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          {tooManyRows && `truncated rows (showing ${MAX_ROWS})`}
          {tooManyRows && tooManyCols && " · "}
          {tooManyCols && `truncated cols (showing ${MAX_COLS})`}
          {content.truncated && (tooManyRows || tooManyCols ? " · " : "") + "file truncated at 512 KB"}
        </div>
      )}
    </div>
  );
}
