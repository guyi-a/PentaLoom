// Todo 区: 拉 GET /sessions/{sid}/todos. 4 个 todo 工具
// (mcp__pentaloom_todos__todo_{write,update,read,delete}) 调用后会改后端 state,
// 监听 liveFrames 里这些 tool_use 出现 → refetch.

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Circle, CircleDot, ListChecks } from "lucide-react";

import { api } from "@/lib/api";
import type { Frame, TodoItem } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  sessionId: string;
  liveFrames: Frame[];
}

const TODO_TOOL_PREFIX = "mcp__pentaloom_todos__";
const MUTATING_TOOLS = new Set([
  "mcp__pentaloom_todos__todo_write",
  "mcp__pentaloom_todos__todo_update",
  "mcp__pentaloom_todos__todo_delete",
]);

function lastTodoToolUseId(live: Frame[]): string | null {
  // 找最近一次会变更 state 的 todo 工具调用. todo_read 不改 state, 不触发 refetch.
  for (let i = live.length - 1; i >= 0; i--) {
    const f = live[i];
    if (
      f.type === "tool_use" &&
      typeof f.name === "string" &&
      f.name.startsWith(TODO_TOOL_PREFIX) &&
      MUTATING_TOOLS.has(f.name)
    ) {
      return f.id;
    }
  }
  return null;
}

export function TodoSection({ sessionId, liveFrames }: Props) {
  const [todos, setTodos] = useState<TodoItem[] | null>(null);

  // 监听 live 流里的 todo 工具调用 — 见到新的就 refetch
  const triggerId = useMemo(() => lastTodoToolUseId(liveFrames), [liveFrames]);

  useEffect(() => {
    let cancelled = false;
    api
      .getTodos(sessionId)
      .then((r) => {
        if (!cancelled) setTodos(r.todos);
      })
      .catch(() => {
        if (!cancelled) setTodos([]);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, triggerId]);

  const counts = useMemo(() => {
    if (!todos) return null;
    return {
      total: todos.length,
      done: todos.filter((t) => t.status === "completed").length,
      active: todos.filter((t) => t.status === "in_progress").length,
    };
  }, [todos]);

  return (
    <section className="border-b border-[color:var(--color-line-soft)] px-3 py-3">
      <div className="mb-2 flex items-center gap-1.5">
        <ListChecks size={11} className="shrink-0 text-[color:var(--color-ink-dim)]" />
        <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">Todo</span>
        {counts && (
          <span className="tabular ml-auto font-mono text-[10.5px] text-[color:var(--color-ink)]">
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
        <div className="px-1 py-2 font-display text-[12px] italic text-[color:var(--color-ink)]">
          {todos === null
            ? "Loading…"
            : "No todo list yet."}
        </div>
      ) : (
        <ul className="space-y-1">
          {todos.map((t) => (
            <li key={t.seq} className="flex items-start gap-2 px-1 py-0.5">
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
