// Todo 区: 取 (history + liveFrames) 里最近一次 TodoWrite tool_use 的 input.todos,
// 渲染 checklist. TodoWrite 是 overwrite 语义 (SDK client-side state), 取最新一次即可,
// 不需要 merge 历史多次.
//
// 状态 icon: pending=○ in_progress=▶ completed=✓ (跟 lucide 对齐).

import { useMemo } from "react";
import { CheckCircle2, Circle, CircleDot, ListChecks } from "lucide-react";

import type { Frame, HistoryMessage, ToolUseFrame } from "@/lib/types";
import { cn } from "@/lib/utils";

interface TodoItem {
  content: string;
  status: "pending" | "in_progress" | "completed";
  activeForm?: string;
}

interface Props {
  history: HistoryMessage[];
  liveFrames: Frame[];
}

function isTodoWriteUse(f: Frame): f is ToolUseFrame {
  return f.type === "tool_use" && f.name === "TodoWrite";
}

function extractTodos(history: HistoryMessage[], live: Frame[]): TodoItem[] | null {
  // 把 history.flat + live 拼成一个有序的 frame 流, 从末尾找最近一次 TodoWrite
  const all: Frame[] = [];
  for (const m of history) all.push(...m.frames);
  all.push(...live);
  for (let i = all.length - 1; i >= 0; i--) {
    const f = all[i];
    if (isTodoWriteUse(f)) {
      const raw = (f.input as Record<string, unknown>)?.todos;
      if (Array.isArray(raw)) {
        return raw
          .filter((x): x is Record<string, unknown> => x !== null && typeof x === "object")
          .map((x) => ({
            content: String(x.content ?? ""),
            status:
              x.status === "in_progress" || x.status === "completed"
                ? x.status
                : "pending",
            activeForm: typeof x.activeForm === "string" ? x.activeForm : undefined,
          }));
      }
    }
  }
  return null;
}

export function TodoSection({ history, liveFrames }: Props) {
  const todos = useMemo(
    () => extractTodos(history, liveFrames),
    [history, liveFrames],
  );

  const counts = useMemo(() => {
    if (!todos) return null;
    return {
      total: todos.length,
      done: todos.filter((t) => t.status === "completed").length,
      active: todos.filter((t) => t.status === "in_progress").length,
    };
  }, [todos]);

  return (
    <section className="border-b border-[color:var(--color-line)] px-3 py-3">
      <div className="mb-2 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.15em] text-[color:var(--color-ink-dim)]">
        <ListChecks size={11} />
        <span>todo</span>
        {counts && (
          <span className="ml-auto font-mono text-[10px] tracking-normal text-[color:var(--color-ink)]">
            {counts.done}/{counts.total}
            {counts.active > 0 && (
              <span className="ml-1 text-[color:var(--color-accent)]">
                · {counts.active} active
              </span>
            )}
          </span>
        )}
      </div>

      {!todos || todos.length === 0 ? (
        <div className="px-1 py-2 text-[11px] italic text-[color:var(--color-ink)]">
          {todos === null
            ? "No todo list yet."
            : "Todo list is empty."}
        </div>
      ) : (
        <ul className="space-y-1">
          {todos.map((t, i) => (
            <li key={i} className="flex items-start gap-2 px-1 py-0.5">
              <span className="mt-0.5 shrink-0">
                {t.status === "completed" ? (
                  <CheckCircle2
                    size={12}
                    className="text-[color:var(--color-accent)]"
                  />
                ) : t.status === "in_progress" ? (
                  <CircleDot
                    size={12}
                    className="text-[color:var(--color-accent)]"
                  />
                ) : (
                  <Circle
                    size={12}
                    className="text-[color:var(--color-ink-dim)]"
                  />
                )}
              </span>
              <span
                className={cn(
                  "min-w-0 flex-1 text-[12px] leading-relaxed",
                  t.status === "completed"
                    ? "text-[color:var(--color-ink)] line-through decoration-[color:var(--color-ink-dim)]"
                    : t.status === "in_progress"
                      ? "font-medium text-[color:var(--color-paper)]"
                      : "text-[color:var(--color-paper-dim)]",
                )}
              >
                {t.status === "in_progress" && t.activeForm
                  ? t.activeForm
                  : t.content}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
