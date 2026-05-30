// Context 区: 累积本会话所有 tool_use 涉及的文件路径 chip.
// 去重 + 按最近调用倒序 — 用户更可能想点最近读/写过的文件.
// 点击 chip → api.openFile (用系统默认 app 打开).
// hover tooltip 显示完整路径.

import { useMemo } from "react";
import { Files } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { Frame, HistoryMessage } from "@/lib/types";
import { PATH_KEYS, basename, extOf, iconForExt } from "@/lib/tool-meta";

interface Props {
  sessionId: string;
  history: HistoryMessage[];
  liveFrames: Frame[];
}

function extractPaths(history: HistoryMessage[], live: Frame[]): string[] {
  // 拼成一个 frame 流, 从尾向前扫 — 同 path 第一次见 (即最近) 算
  const all: Frame[] = [];
  for (const m of history) all.push(...m.frames);
  all.push(...live);
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (let i = all.length - 1; i >= 0; i--) {
    const f = all[i];
    if (f.type !== "tool_use") continue;
    const input = f.input as Record<string, unknown> | undefined;
    if (!input) continue;
    for (const k of PATH_KEYS) {
      const v = input[k];
      if (typeof v === "string" && v.startsWith("/") && !seen.has(v)) {
        seen.add(v);
        ordered.push(v);
      }
    }
  }
  return ordered;
}

export function ContextSection({ sessionId, history, liveFrames }: Props) {
  const paths = useMemo(
    () => extractPaths(history, liveFrames),
    [history, liveFrames],
  );

  async function openPath(path: string) {
    try {
      await api.openFile({ sessionId, path });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  return (
    <section className="px-3 py-3">
      <div className="mb-2 flex items-center gap-1.5">
        <Files size={11} className="shrink-0 text-[color:var(--color-ink-dim)]" />
        <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">Context</span>
        {paths.length > 0 && (
          <span className="tabular ml-auto font-mono text-[10.5px] text-[color:var(--color-ink)]">
            {paths.length} file{paths.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {paths.length === 0 ? (
        <div className="px-1 py-2 font-display text-[12px] italic text-[color:var(--color-ink)]">
          Files touched by tools will appear here.
        </div>
      ) : (
        <ul className="max-h-[40vh] space-y-0.5 overflow-y-auto pr-1">
          {paths.map((p) => {
            const ext = extOf(p);
            const Icon = iconForExt(ext);
            return (
              <li key={p}>
                <button
                  type="button"
                  onClick={() => openPath(p)}
                  title={p}
                  className="group flex w-full items-center gap-2 rounded-[4px] px-1.5 py-1 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]"
                >
                  <Icon
                    size={12}
                    className="shrink-0 text-[color:var(--color-thread-file)]"
                  />
                  <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)] group-hover:text-[color:var(--color-paper)]">
                    {basename(p)}
                  </span>
                  {ext && (
                    <span className="shrink-0 font-mono text-[9.5px] uppercase text-[color:var(--color-ink-dim)]">
                      {ext}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
