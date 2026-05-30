import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { Check, GitBranch, Pencil, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { LoomMark } from "@/components/brand/LoomMark";
import { SidebarGroup } from "@/components/sidebar/SidebarGroup";
import { api } from "@/lib/api";
import type { SessionMeta } from "@/lib/types";
import {
  cn,
  formatRelative,
  getTimeGroup,
  shortenSid,
  TIME_GROUP_ORDER,
  type TimeGroup,
} from "@/lib/utils";

interface Props {
  sessions: SessionMeta[];
  currentSid?: string;
  onChanged: () => void;
}

// 空状态: brand moment, 不浪费这块画布.
function EmptyState() {
  return (
    <div className="flex flex-col items-center px-5 py-12 text-center">
      <LoomMark size={28} active={false} className="mb-3 opacity-60" />
      <p className="font-display text-[14px] italic leading-snug text-[color:var(--color-paper-dim)]">
        No threads yet,
        <br />
        weave one.
      </p>
      <p className="mt-2 text-[11px] text-[color:var(--color-ink)]">
        Click + above to start.
      </p>
    </div>
  );
}

export function SessionList({ sessions, currentSid, onChanged }: Props) {
  // 时间分组 — 按 last_active_at 落桶. SidebarGroup label "Threads" 顶层包住,
  // M12 加 app_gen 时在它前面加个 <SidebarGroup label="Apps">, 这里代码零改.
  const grouped = useMemo(() => {
    const buckets = new Map<TimeGroup, SessionMeta[]>();
    for (const s of sessions) {
      const g = getTimeGroup(s.last_active_at);
      const arr = buckets.get(g) ?? [];
      arr.push(s);
      buckets.set(g, arr);
    }
    // 桶内按 last_active_at desc (后端可能已排, 兜底再排一次)
    for (const arr of buckets.values()) {
      arr.sort(
        (a, b) =>
          new Date(b.last_active_at).getTime() -
          new Date(a.last_active_at).getTime(),
      );
    }
    return buckets;
  }, [sessions]);

  if (sessions.length === 0) {
    return (
      <>
        <ProjectsPlaceholder />
        <EmptyState />
      </>
    );
  }

  return (
    <>
      <ProjectsPlaceholder />
      <SidebarGroup label="Threads">
        <div className="space-y-3">
          {TIME_GROUP_ORDER.map((group) => {
            const items = grouped.get(group);
            if (!items || items.length === 0) return null;
            return (
              <div key={group}>
                {/* 时间子分组标题 — Fraunces italic 跟主标题同款, 缩进一点 */}
                <div className="mb-1 px-4 font-display text-[11px] italic text-[color:var(--color-ink-dim)]">
                  {group}
                </div>
                <ul className="space-y-0.5 px-2">
                  {items.map((s) => (
                    <SessionRow
                      key={s.session_id}
                      session={s}
                      active={s.session_id === currentSid}
                      onChanged={onChanged}
                    />
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      </SidebarGroup>
    </>
  );
}

// Projects 分组占位 — 后端 Project 模型 + app_gen 能力上 M12 时这里换成真渲染.
// 默认折叠克制, 用户展开能看到 "coming with app_gen" 提示, 不假装有"New project"按钮.
function ProjectsPlaceholder() {
  return (
    <SidebarGroup label="Projects" defaultExpanded={false}>
      <div className="mx-2 rounded-[6px] px-3 py-2 text-[11px] leading-relaxed text-[color:var(--color-ink-dim)]">
        <p className="font-display italic">No projects yet.</p>
        <p className="mt-0.5">Coming with app_gen capability.</p>
      </div>
    </SidebarGroup>
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
        title={`${displayTitle} · ${session.mounted_dirs.length} mount${session.mounted_dirs.length !== 1 ? "s" : ""}`}
        className={cn(
          "group relative rounded-[8px] transition-all duration-150",
          active
            ? "bg-[color:var(--color-bg-card)] shadow-[0_1px_2px_rgba(20,30,50,0.04)]"
            : "hover:bg-[color:var(--color-bg-raised)]",
          (editing || confirmingDelete) && "cursor-default",
        )}
      >
        {/* active 左侧 2px 钢蓝竖条 — file 主色, 视觉锚 + 强 active 信号 */}
        {active && (
          <span className="pointer-events-none absolute left-0 top-2 bottom-2 w-[2px] rounded-r bg-[color:var(--color-thread-file)]" />
        )}

        {confirmingDelete ? (
          <div
            className="flex items-center justify-between gap-2 px-3 py-2 text-[11px] text-[color:var(--color-paper-dim)]"
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
        ) : editing ? (
          <div className="flex items-center gap-1 px-3 py-2">
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
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={(e) => {
                e.stopPropagation();
                commitRename();
              }}
              className="rounded-[3px] p-0.5 text-[color:var(--color-accent)] hover:bg-[color:var(--color-bg-raised)]"
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
              className="rounded-[3px] p-0.5 text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-raised)]"
              title="Cancel rename"
            >
              <X size={12} />
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2 px-3 py-2.5">
            {/* GitBranch 染 thread-file 钢蓝, opacity 50 — 五瓣丝线主题首次散到列表里, 一眼看出"是 PentaLoom 不是 Linear" */}
            <GitBranch
              size={12}
              className="shrink-0 text-[color:var(--color-thread-file)] opacity-50"
            />
            {/* title */}
            <div
              className={cn(
                "min-w-0 flex-1 truncate text-[13px] leading-snug",
                active
                  ? "font-medium text-[color:var(--color-paper)]"
                  : "text-[color:var(--color-paper-dim)]",
              )}
            >
              {session.title ?? (
                <span className="font-mono text-[12px] text-[color:var(--color-ink)]">
                  {fallbackTitle}
                </span>
              )}
            </div>
            {/* 右侧: 时间 (默认显) ↔ rename/delete 按钮 (hover 显) */}
            <span className="tabular shrink-0 font-mono text-[10.5px] text-[color:var(--color-ink)] transition-opacity group-hover:opacity-0">
              {formatRelative(session.last_active_at)}
            </span>
            <div className="pointer-events-none absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmingDelete(false);
                  setEditing(true);
                }}
                className="rounded-[3px] bg-[color:var(--color-bg-card)] p-0.5 text-[color:var(--color-ink)] shadow-sm hover:text-[color:var(--color-paper)]"
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
                className="rounded-[3px] bg-[color:var(--color-bg-card)] p-0.5 text-[color:var(--color-ink)] shadow-sm hover:text-[color:var(--color-error)]"
                title="Delete thread"
              >
                <Trash2 size={11} />
              </button>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}
