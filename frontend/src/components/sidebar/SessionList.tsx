import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import {
  AlertCircle,
  Ellipsis,
  GitBranch,
  Loader2,
  Pencil,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { LoomMark } from "@/components/brand/LoomMark";
import { SidebarGroup } from "@/components/sidebar/SidebarGroup";
import { WeaverGroup } from "@/components/sidebar/WeaverGroup";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useSessionStatus } from "@/lib/session-status-store";
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
  // 时间分组 — 按 last_active_at 落桶. SidebarGroup label "Threads" 顶层包住.
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
        <WeaverGroup currentSid={currentSid} />
        <EmptyState />
      </>
    );
  }

  return (
    <>
      <WeaverGroup currentSid={currentSid} />
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
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [draft, setDraft] = useState(session.title ?? "");
  const [busy, setBusy] = useState<null | "rename" | "delete">(null);
  const [menuOpen, setMenuOpen] = useState(false);
  // dialog 关闭过渡期 session 引用还在但 title 显示要保持 — 防 dialog fade-out
  // 时 description 文字塌陷成空.
  const lastDeleteTitleRef = useRef<string>("");

  useEffect(() => {
    if (!renameOpen) setDraft(session.title ?? "");
  }, [renameOpen, session.title]);

  const fallbackTitle = shortenSid(session.session_id);
  const displayTitle = session.title || fallbackTitle;

  function openRename() {
    setDraft(session.title ?? "");
    setRenameOpen(true);
  }

  function openDelete() {
    lastDeleteTitleRef.current = displayTitle;
    setDeleteOpen(true);
  }

  async function commitRename() {
    const next = draft.trim();
    if (next === (session.title ?? "")) {
      setRenameOpen(false);
      return;
    }
    setBusy("rename");
    try {
      await api.patchSession(session.session_id, { title: next === "" ? null : next });
      toast.success("Renamed");
      setRenameOpen(false);
      onChanged();
    } catch (err) {
      toast.error(`Rename failed: ${String(err)}`);
    } finally {
      setBusy(null);
    }
  }

  async function deleteSession() {
    setBusy("delete");
    try {
      await api.deleteSession(session.session_id);
      toast.success("Deleted");
      setDeleteOpen(false);
      onChanged();
      if (active) {
        // Radix AlertDialog 卸时给 body 加的 pointer-events:none 在
        // 紧跟着 navigate 让 ChatPage 同时 unmount 时偶尔不还原,
        // 表现为删完会话后整页点不动. 等一帧让 Radix cleanup 跑完再切.
        requestAnimationFrame(() => navigate("/"));
      }
    } catch (err) {
      toast.error(`Delete failed: ${String(err)}`);
    } finally {
      setBusy(null);
      // 兜底: Radix 偶尔漏还原 body pointer-events. 下一帧检查一次.
      requestAnimationFrame(() => {
        if (document.body.style.pointerEvents === "none") {
          document.body.style.pointerEvents = "";
        }
      });
    }
  }

  // hover 时让 ⋯ 顶替时间戳显示. menuOpen 时强制 ⋯ 可见 (即使鼠标已挪开).
  const showMenuTrigger = menuOpen;

  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        onClick={() => navigate(`/s/${session.session_id}`)}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          navigate(`/s/${session.session_id}`);
        }}
        title={`${displayTitle} · ${session.mounted_dirs.length} mount${session.mounted_dirs.length !== 1 ? "s" : ""}`}
        className={cn(
          "group relative rounded-[8px] transition-all duration-150",
          // 三层级视觉:
          //   idle    → 透明 (= sidebar bg-soft 米灰)
          //   active  → bg-card 纯白 + shadow + 钢蓝竖条 (PentaLoom brand)
          //   hover   → idle 行变 bg-raised 冷灰; active 行不动 bg, 仅加深 shadow
          //             轻微浮起 — active 焦点态保持视觉稳定, 不被 hover 干扰
          active
            ? "bg-[color:var(--color-bg-card)] shadow-[0_1px_2px_rgba(20,30,50,0.04)] hover:shadow-[0_2px_8px_rgba(20,30,50,0.08)]"
            : "hover:bg-[color:var(--color-bg-raised)]",
        )}
      >
        {/* active 左侧 2px 钢蓝竖条 — file 主色, 视觉锚 + 强 active 信号 */}
        {active && (
          <span className="pointer-events-none absolute left-0 top-2 bottom-2 w-[2px] rounded-r bg-[color:var(--color-thread-file)]" />
        )}

        <div className="flex items-center gap-2 px-3 py-2.5">
          {/* 状态 icon: idle GitBranch / running spinner / waiting_approval AlertCircle */}
          <SessionStatusIcon sid={session.session_id} />
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
          {/* 右侧: 时间 (默认显) ↔ ⋯ 菜单触发 (hover 或菜单打开时显). 同尺寸占位防跳动. */}
          <div className="relative flex w-12 shrink-0 items-center justify-end">
            <span
              className={cn(
                "tabular font-mono text-[10.5px] text-[color:var(--color-ink)] transition-opacity",
                showMenuTrigger ? "opacity-0" : "group-hover:opacity-0",
              )}
            >
              {formatRelative(session.last_active_at)}
            </span>
            <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  onClick={(e) => e.stopPropagation()}
                  className={cn(
                    "absolute right-0 inline-flex h-6 w-6 items-center justify-center rounded-[5px] text-[color:var(--color-ink)] transition-opacity hover:bg-[color:var(--color-line)] hover:text-[color:var(--color-paper)]",
                    showMenuTrigger ? "opacity-100" : "opacity-0 group-hover:opacity-100",
                  )}
                  title="More actions"
                >
                  <Ellipsis size={14} />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                onClick={(e) => e.stopPropagation()}
                onCloseAutoFocus={(e) => e.preventDefault()}
              >
                <DropdownMenuItem onSelect={() => openRename()}>
                  <Pencil />
                  <span>Rename</span>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onSelect={() => openDelete()}>
                  <Trash2 />
                  <span>Delete</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </div>

      {/* Rename Dialog — autoFocus + Enter 保存 / Esc 关 + 显式 Save/Cancel.
          不靠 onBlur 自动提交, 避免点 backdrop / 切焦点时误提交. */}
      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent
          className="w-[min(420px,90vw)] rounded-[12px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] p-5 shadow-[0_20px_60px_rgba(20,30,50,0.18)]"
          onOpenAutoFocus={(e) => {
            // 自定义 focus — 让 input autoFocus 生效, 不被 Dialog 默认 focus 抢走
            e.preventDefault();
          }}
        >
          <h2 className="font-display text-[16px] font-medium tracking-[-0.005em] text-[color:var(--color-paper)]">
            Rename thread
          </h2>
          <p className="mt-1 text-[12px] text-[color:var(--color-paper-dim)]">
            Give this thread a new title (or leave empty to use the session id).
          </p>
          <input
            autoFocus
            value={draft}
            disabled={busy === "rename"}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitRename();
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setRenameOpen(false);
              }
            }}
            placeholder={fallbackTitle}
            className="mt-3 block w-full rounded-[6px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-2.5 py-1.5 text-[13px] text-[color:var(--color-paper)] focus:border-[color:var(--color-accent)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-accent)]/15 disabled:cursor-not-allowed disabled:opacity-60"
          />
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setRenameOpen(false)}
              disabled={busy === "rename"}
              className="rounded-[6px] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)] disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={commitRename}
              disabled={busy === "rename"}
              className="rounded-[6px] bg-[color:var(--color-accent)] px-3 py-1.5 text-[12px] text-white transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {busy === "rename" ? "Saving…" : "Save"}
            </button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete AlertDialog — title 走 lastDeleteTitleRef 防关闭过渡期文字塌陷 */}
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogTitle>Delete this thread?</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="text-[color:var(--color-paper)]">
              "{lastDeleteTitleRef.current}"
            </span>
            {" 跟它的所有消息会被永久删除. 这一步不可恢复."}
          </AlertDialogDescription>
          <div className="mt-4 flex justify-end gap-2">
            <AlertDialogCancel asChild>
              <button
                type="button"
                disabled={busy === "delete"}
                className="rounded-[6px] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)] disabled:opacity-50"
              >
                Cancel
              </button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <button
                type="button"
                onClick={deleteSession}
                disabled={busy === "delete"}
                className="rounded-[6px] bg-[color:var(--color-error)] px-3 py-1.5 text-[12px] text-white transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {busy === "delete" ? "Deleting…" : "Delete"}
              </button>
            </AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}

// 单 session 状态 icon — 订阅 useSessionStatusStore Map<sid, status>, 状态变
// 时仅本 SessionRow 重渲 (zustand 选择器精度). 三态:
//   - idle: GitBranch (默认 brand-y 钢蓝, opacity 50 跟非 active 文字一档)
//   - running: Loader2 animate-spin (accent 色, 暗示"在跑")
//   - waiting_approval: AlertCircle 橙色 (用户进 session 解审批, 高显著度)
function SessionStatusIcon({ sid }: { sid: string }) {
  const status = useSessionStatus(sid);
  if (status === "running") {
    return (
      <Loader2
        size={12}
        className="shrink-0 animate-spin text-[color:var(--color-accent)]"
      />
    );
  }
  if (status === "waiting_approval") {
    return (
      <AlertCircle
        size={12}
        className="shrink-0 text-[color:var(--color-warn,#d97706)]"
      />
    );
  }
  return (
    <GitBranch
      size={12}
      className="shrink-0 text-[color:var(--color-thread-file)] opacity-50"
    />
  );
}
