// sqlite 数据库预览 — 后端 sqlite3 stdlib 只读模式打开, 出表名/列名/前 200 行/行数.
//
// 顶部 tab 切表 (sqlite 表名一般比 xlsx sheet 名短, 放顶部更直觉);
// 表格 sticky header + 行号列 + 单元格 truncate; cap 200 行 × 50 列, 超了
// 提示 truncated.

import { useEffect, useRef, useState } from "react";
import { Database, ExternalLink, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { DatabasePreview as DatabasePreviewData } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  sessionId: string;
  path: string;
}

export function DatabasePreview({ sessionId, path }: Props) {
  const [data, setData] = useState<DatabasePreviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeTable, setActiveTable] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    setActiveTable(0);

    api
      .getPreviewSqlite(sessionId, path)
      .then((result) => {
        if (cancelled) return;
        setData(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "sqlite 加载失败");
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
          sqlite 预览失败: {error}
        </div>
        <button
          type="button"
          onClick={openWithSystem}
          className="flex items-center gap-1.5 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
        >
          <ExternalLink size={11} />
          Open with default app
        </button>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex h-full items-center justify-center text-[color:var(--color-ink)]">
        <Loader2 size={14} className="animate-spin" />
      </div>
    );
  }

  if (data.tables.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-[color:var(--color-ink)]">
        <Database size={20} className="text-[color:var(--color-ink-dim)]" />
        <div className="text-[12px]">数据库为空 — 没有用户表</div>
      </div>
    );
  }

  const table = data.tables[activeTable];

  return (
    <div className="flex h-full min-h-0 flex-col bg-[color:var(--color-bg-card)]">
      {/* 顶部 tab 栏切表 */}
      <div className="shrink-0 border-b border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)]">
        <div className="scrollbar-hidden flex items-center overflow-x-auto">
          {data.tables.map((t, i) => (
            <button
              key={t.name}
              type="button"
              onClick={() => {
                setActiveTable(i);
                scrollRef.current?.scrollTo(0, 0);
              }}
              className={cn(
                "flex items-center gap-1.5 border-r border-[color:var(--color-line)] px-3 py-1.5 text-[11px] whitespace-nowrap transition-colors",
                i === activeTable
                  ? "bg-[color:var(--color-bg-card)] font-medium text-[color:var(--color-paper)]"
                  : "text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper-dim)]",
              )}
              title={`${t.row_count} rows`}
            >
              <Database size={11} className="text-[color:var(--color-ink-dim)]" />
              <span>{t.name}</span>
              <span className="font-mono text-[10px] text-[color:var(--color-ink-dim)]">
                {t.row_count}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* 表格 */}
      <div ref={scrollRef} className="scrollbar-hidden min-h-0 flex-1 overflow-auto">
        {table && (
          <table className="border-collapse font-mono text-[11px]">
            <thead>
              <tr>
                <th className="sticky left-0 top-0 z-30 h-[24px] w-[40px] border-r border-b border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] text-[10px] font-normal text-[color:var(--color-ink-dim)] select-none" />
                {table.columns.map((col) => (
                  <th
                    key={col}
                    className="sticky top-0 z-20 h-[24px] min-w-[100px] border-r border-b border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-2 text-left text-[10.5px] font-medium text-[color:var(--color-paper-dim)] select-none"
                    title={col}
                  >
                    <span className="block truncate">{col}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, r) => (
                <tr key={r}>
                  <td className="sticky left-0 z-10 h-[22px] w-[40px] border-r border-b border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] text-center text-[10px] font-normal text-[color:var(--color-ink-dim)] select-none">
                    {r + 1}
                  </td>
                  {table.columns.map((_, c) => {
                    const cell = row[c] ?? "";
                    return (
                      <td
                        key={c}
                        title={cell}
                        className="h-[22px] max-w-[400px] border-r border-b border-[color:var(--color-line-soft)] px-2 text-[color:var(--color-paper-dim)]"
                      >
                        <span className="block truncate">{cell}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 底部 truncated 提示 */}
      {(table?.truncated || data.truncated) && (
        <div className="shrink-0 border-t border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-3 py-1 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          truncated · {table?.row_count ?? 0} rows × {table?.columns.length ?? 0} cols (showing first {table?.rows.length ?? 0})
        </div>
      )}
    </div>
  );
}
