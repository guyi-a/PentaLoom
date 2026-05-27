import { useNavigate } from "react-router";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { SessionMeta } from "@/lib/types";
import { cn, formatRelative, shortenSid } from "@/lib/utils";

interface Props {
  sessions: SessionMeta[];
  currentSid?: string;
  onChanged: () => void;
}

export function SessionList({ sessions, currentSid, onChanged }: Props) {
  const navigate = useNavigate();

  if (sessions.length === 0) {
    return (
      <div className="px-5 py-6 text-[12px] leading-relaxed text-[color:var(--color-ink)]">
        No threads yet.
        <br />
        <span className="text-[color:var(--color-ink-dim)]">
          Click + above to start one.
        </span>
      </div>
    );
  }

  return (
    <ul className="space-y-px px-2 py-1">
      {sessions.map((s) => {
        const active = s.session_id === currentSid;
        return (
          <li key={s.session_id}>
            <button
              type="button"
              onClick={() => navigate(`/s/${s.session_id}`)}
              className={cn(
                "group relative flex w-full flex-col gap-0.5 rounded-[5px] border px-3 py-2 text-left transition-colors",
                active
                  ? "border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-raised)]"
                  : "border-transparent hover:bg-[color:var(--color-bg-raised)]",
              )}
            >
              <div className="flex items-baseline justify-between gap-2">
                <div
                  className={cn(
                    "truncate text-[13px] leading-snug",
                    active
                      ? "font-medium text-[color:var(--color-paper)]"
                      : "text-[color:var(--color-paper-dim)]",
                  )}
                >
                  {s.title ?? (
                    <span className="font-mono text-[12px] text-[color:var(--color-ink)]">
                      {shortenSid(s.session_id)}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between font-mono text-[10px] text-[color:var(--color-ink)]">
                <span>{formatRelative(s.last_active_at)}</span>
                <span className="tabular">
                  {s.mounted_dirs.length} mount
                  {s.mounted_dirs.length !== 1 ? "s" : ""}
                </span>
              </div>

              {/* delete - hover 显示 */}
              <span
                role="button"
                tabIndex={0}
                onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete thread ${shortenSid(s.session_id)}?`)) return;
                  try {
                    await api.deleteSession(s.session_id);
                    toast.success("Deleted");
                    if (active) navigate("/");
                    onChanged();
                  } catch (err) {
                    toast.error(`Delete failed: ${String(err)}`);
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key !== "Enter" && e.key !== " ") return;
                  e.stopPropagation();
                  (e.target as HTMLElement).click();
                }}
                className="absolute right-2 top-2 hidden text-[12px] text-[color:var(--color-ink)] hover:text-[color:var(--color-error)] group-hover:inline-block"
              >
                ×
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
