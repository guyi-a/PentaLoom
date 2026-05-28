import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { Check, Pencil, Trash2, X } from "lucide-react";
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
      {sessions.map((session) => (
        <SessionRow
          key={session.session_id}
          session={session}
          active={session.session_id === currentSid}
          onChanged={onChanged}
        />
      ))}
    </ul>
  );
}

function SessionRow({
  session,
  active,
  onChanged,
}: {
  session: SessionMeta;
  active: boolean;
  onChanged: () => void;
}) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.title ?? "");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [busy, setBusy] = useState<null | "rename" | "delete">(null);

  useEffect(() => {
    if (!editing) setDraft(session.title ?? "");
  }, [editing, session.title]);

  const fallbackTitle = shortenSid(session.session_id);
  const displayTitle = session.title || fallbackTitle;

  async function commitRename() {
    const next = draft.trim();
    setEditing(false);
    if (next === (session.title ?? "")) return;
    setBusy("rename");
    try {
      await api.patchSession(session.session_id, { title: next === "" ? null : next });
      toast.success("Renamed");
      onChanged();
    } catch (err) {
      toast.error(`Rename failed: ${String(err)}`);
      setDraft(session.title ?? "");
    } finally {
      setBusy(null);
    }
  }

  async function deleteSession() {
    setBusy("delete");
    try {
      await api.deleteSession(session.session_id);
      toast.success("Deleted");
      if (active) navigate("/");
      onChanged();
    } catch (err) {
      toast.error(`Delete failed: ${String(err)}`);
    } finally {
      setBusy(null);
      setConfirmingDelete(false);
    }
  }

  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        onClick={() => {
          if (editing || confirmingDelete) return;
          navigate(`/s/${session.session_id}`);
        }}
        onKeyDown={(e) => {
          if (editing || confirmingDelete) return;
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          navigate(`/s/${session.session_id}`);
        }}
        className={cn(
          "group relative flex w-full flex-col gap-1 rounded-[5px] border px-3 py-2 text-left transition-colors",
          active
            ? "border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-raised)]"
            : "border-transparent hover:bg-[color:var(--color-bg-raised)]",
          (editing || confirmingDelete) && "cursor-default",
        )}
      >
        <div className="flex min-w-0 items-start gap-2 pr-10">
          {editing ? (
            <input
              autoFocus
              value={draft}
              disabled={busy === "rename"}
              onChange={(e) => setDraft(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  (e.target as HTMLInputElement).blur();
                }
                if (e.key === "Escape") {
                  e.preventDefault();
                  setDraft(session.title ?? "");
                  setEditing(false);
                }
              }}
              placeholder={fallbackTitle}
              className="min-w-0 flex-1 rounded-[4px] border border-[color:var(--color-accent)] bg-[color:var(--color-bg-card)] px-1.5 py-0.5 text-[13px] text-[color:var(--color-paper)] focus:outline-none"
            />
          ) : (
            <div
              className={cn(
                "min-w-0 flex-1 truncate text-[13px] leading-snug",
                active
                  ? "font-medium text-[color:var(--color-paper)]"
                  : "text-[color:var(--color-paper-dim)]",
              )}
              title={displayTitle}
            >
              {session.title ?? (
                <span className="font-mono text-[12px] text-[color:var(--color-ink)]">
                  {fallbackTitle}
                </span>
              )}
            </div>
          )}
        </div>

        {confirmingDelete ? (
          <div
            className="flex items-center justify-between gap-2 rounded-[4px] border border-[color:var(--color-error)]/25 bg-[color:var(--color-bg-card)] px-2 py-1 text-[11px] text-[color:var(--color-paper-dim)]"
            onClick={(e) => e.stopPropagation()}
          >
            <span className="truncate">Delete this thread?</span>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                disabled={busy === "delete"}
                onClick={deleteSession}
                className="rounded-[3px] px-1.5 py-0.5 text-[color:var(--color-error)] hover:bg-[color:var(--color-error)]/10 disabled:opacity-50"
              >
                {busy === "delete" ? "…" : "Delete"}
              </button>
              <button
                type="button"
                disabled={busy === "delete"}
                onClick={() => setConfirmingDelete(false)}
                className="rounded-[3px] px-1.5 py-0.5 text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-raised)] disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between font-mono text-[10px] text-[color:var(--color-ink)]">
            <span>{formatRelative(session.last_active_at)}</span>
            <span className="tabular">
              {session.mounted_dirs.length} mount
              {session.mounted_dirs.length !== 1 ? "s" : ""}
            </span>
          </div>
        )}

        {!editing && !confirmingDelete && (
          <div className="absolute right-2 top-2 hidden items-center gap-1 group-hover:flex">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setConfirmingDelete(false);
                setEditing(true);
              }}
              className="rounded-[3px] p-0.5 text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)]"
              title="Rename thread"
            >
              <Pencil size={11} />
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(false);
                setConfirmingDelete(true);
              }}
              className="rounded-[3px] p-0.5 text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-error)]"
              title="Delete thread"
            >
              <Trash2 size={11} />
            </button>
          </div>
        )}

        {editing && (
          <div className="absolute right-2 top-2 flex items-center gap-1">
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={(e) => {
                e.stopPropagation();
                commitRename();
              }}
              className="rounded-[3px] p-0.5 text-[color:var(--color-accent)] hover:bg-[color:var(--color-bg-card)]"
              title="Save title"
            >
              <Check size={12} />
            </button>
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={(e) => {
                e.stopPropagation();
                setDraft(session.title ?? "");
                setEditing(false);
              }}
              className="rounded-[3px] p-0.5 text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-card)]"
              title="Cancel rename"
            >
              <X size={12} />
            </button>
          </div>
        )}
      </div>
    </li>
  );
}
