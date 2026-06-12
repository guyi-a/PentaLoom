// xlsx 预览 — 后端 openpyxl 解析 cells + styles + merges, 前端 HTML table 真渲.
//
// 含 sticky header (列字母 A/B/C... + 行号 1/2/3...) / 字体颜色 / 背景色 / bold /
// 合并单元格 / sheet tabs (workbook 多 sheet 时底部切换). cap 200×50 cells,
// 超了底部显示 truncated 提示.

import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type {
  XlsxCellStyle,
  XlsxMerge,
  XlsxWorkbookPreview,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  sessionId: string;
  path: string;
}

export function XlsxPreview({ sessionId, path }: Props) {
  const [workbook, setWorkbook] = useState<XlsxWorkbookPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeSheet, setActiveSheet] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setWorkbook(null);
    setError(null);
    setActiveSheet(0);

    api
      .getPreviewXlsx(sessionId, path)
      .then((wb) => {
        if (cancelled) return;
        setWorkbook(wb);
        setActiveSheet(wb.active_sheet_index);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "xlsx 加载失败");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId, path]);

  async function openWithSystem() {
    try {
      await api.openFile({ sessionId, path });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <div className="text-[12px] text-[color:var(--color-error)]">
          xlsx 预览失败: {error}
        </div>
        <button
          type="button"
          onClick={openWithSystem}
          className="flex items-center gap-1.5 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
        >
          <ExternalLink size={11} />
          Open with Excel
        </button>
      </div>
    );
  }

  if (!workbook) {
    return (
      <div className="flex h-full items-center justify-center text-[color:var(--color-ink)]">
        <Loader2 size={14} className="animate-spin" />
      </div>
    );
  }

  const sheet = workbook.sheets[activeSheet];
  const dataCols = sheet?.rows[0]?.length ?? 0;
  const dataRows = sheet?.rows.length ?? 0;
  // 至少铺 30 列 / 50 行的"白板", 跟 Excel 视觉接近
  const showCols = Math.max(dataCols, 30);
  const showRows = Math.max(dataRows, 50);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[color:var(--color-bg-card)]">
      {sheet && (
        <div ref={scrollRef} className="scrollbar-hidden min-h-0 flex-1 overflow-auto">
          <table className="border-collapse font-mono text-[11px]">
            <thead>
              <tr>
                <th className="sticky left-0 top-0 z-30 h-[22px] w-[40px] bg-[color:var(--color-bg-soft)] border-r border-b border-[color:var(--color-line)] text-[10px] font-normal text-[color:var(--color-ink-dim)] select-none" />
                {Array.from({ length: showCols }, (_, c) => (
                  <th
                    key={c}
                    className="sticky top-0 z-20 h-[22px] min-w-[80px] bg-[color:var(--color-bg-soft)] border-r border-b border-[color:var(--color-line)] px-1 text-[10px] font-normal text-[color:var(--color-ink-dim)] text-center select-none"
                  >
                    {colLabel(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Array.from({ length: showRows }, (_, r) => (
                <tr key={r}>
                  <td className="sticky left-0 z-10 h-[22px] w-[40px] bg-[color:var(--color-bg-soft)] border-r border-b border-[color:var(--color-line)] text-[10px] text-[color:var(--color-ink-dim)] text-center font-normal select-none">
                    {r + 1}
                  </td>
                  {Array.from({ length: showCols }, (_, c) => {
                    const cell = sheet.rows[r]?.[c];
                    if (!cell) {
                      return (
                        <td
                          key={c}
                          className="h-[22px] border-r border-b border-[color:var(--color-line-soft)]"
                        />
                      );
                    }
                    const merge = getMergeInfo(sheet.merges, r, c);
                    if (merge === "hidden") return null;
                    return (
                      <td
                        key={c}
                        rowSpan={merge?.rowSpan}
                        colSpan={merge?.colSpan}
                        title={cell.text}
                        style={cellStyleToCss(cell.style)}
                        className="h-[22px] border-r border-b border-[color:var(--color-line-soft)] px-1.5 whitespace-nowrap text-[color:var(--color-paper-dim)]"
                      >
                        {cell.text}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 底部 sheet tabs + truncation 提示 */}
      <div className="shrink-0 flex items-center border-t border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)]">
        {workbook.sheets.length > 1 && (
          <div className="scrollbar-hidden flex items-center overflow-x-auto">
            {workbook.sheets.map((s, i) => (
              <button
                key={s.name}
                type="button"
                onClick={() => {
                  setActiveSheet(i);
                  scrollRef.current?.scrollTo(0, 0);
                }}
                className={cn(
                  "border-r border-[color:var(--color-line)] px-3 py-1 text-[11px] whitespace-nowrap transition-colors",
                  i === activeSheet
                    ? "bg-[color:var(--color-bg-card)] font-medium text-[color:var(--color-paper)]"
                    : "text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper-dim)]",
                )}
              >
                {s.name}
              </button>
            ))}
          </div>
        )}
        {(sheet?.truncated || workbook.truncated) && (
          <div className="ml-auto px-3 py-1 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
            truncated · {sheet?.row_count ?? 0} rows × {sheet?.col_count ?? 0} cols total
          </div>
        )}
      </div>
    </div>
  );
}

function colLabel(idx: number): string {
  let label = "";
  let n = idx;
  do {
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return label;
}

function cellStyleToCss(s?: XlsxCellStyle | null): React.CSSProperties | undefined {
  if (!s) return undefined;
  const css: React.CSSProperties = {};
  if (s.bold) css.fontWeight = "bold";
  if (s.italic) css.fontStyle = "italic";
  if (s.font_size) css.fontSize = `${s.font_size}px`;
  if (s.color) css.color = s.color;
  if (s.bg_color) css.backgroundColor = s.bg_color;
  if (s.align) css.textAlign = s.align;
  if (s.valign) css.verticalAlign = s.valign;
  return Object.keys(css).length > 0 ? css : undefined;
}

type MergeInfo = { rowSpan: number; colSpan: number } | "hidden" | null;

function getMergeInfo(merges: XlsxMerge[], row: number, col: number): MergeInfo {
  for (const m of merges) {
    if (row === m.start_row && col === m.start_col) {
      return {
        rowSpan: m.end_row - m.start_row + 1,
        colSpan: m.end_col - m.start_col + 1,
      };
    }
    if (
      row >= m.start_row &&
      row <= m.end_row &&
      col >= m.start_col &&
      col <= m.end_col
    ) {
      return "hidden";
    }
  }
  return null;
}
