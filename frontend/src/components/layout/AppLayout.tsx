import { useEffect, useMemo, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Outlet, useNavigate, useParams } from "react-router";
import { GitBranch, PanelLeftClose, Search, Settings, SquarePen, X } from "lucide-react";
import useSWR, { useSWRConfig } from "swr";

import { SessionList } from "@/components/sidebar/SessionList";
import { LoomMark } from "@/components/brand/LoomMark";
import { api } from "@/lib/api";
import { MAIN_CONTENT_MIN_WIDTH } from "@/lib/layout-constraints";
import { useSessionStatusStore } from "@/lib/session-status-store";
import { formatRelative, shortenSid } from "@/lib/utils";

const SIDEBAR_OPEN_KEY = "pentaloom:left-sidebar:open";
const SIDEBAR_WIDTH_KEY = "pentaloom:left-sidebar:width";
const SIDEBAR_MIN = 260;
const SIDEBAR_DEFAULT = 320;
const SIDEBAR_MAX = 420;
const RIGHT_PANEL_RESERVE = 340;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function initialSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  const saved = window.localStorage.getItem(SIDEBAR_OPEN_KEY);
  if (saved !== null) return saved !== "false";
  return true;
}

function initialSidebarWidth(): number {
  if (typeof window === "undefined") return SIDEBAR_DEFAULT;
  const saved = Number(window.localStorage.getItem(SIDEBAR_WIDTH_KEY));
  return Number.isFinite(saved) ? clamp(saved, SIDEBAR_MIN, SIDEBAR_MAX) : SIDEBAR_DEFAULT;
}

function isElectronShell(): boolean {
  if (typeof window === "undefined") return false;
  return "__PENTALOOM__" in window;
}

// outlet context — ChatPage / EmptyPage 用 useOutletContext<SidebarLayoutContext>()
// 拿到 sidebar 状态 + 展开 callback, 自行在自己 header 里处理三圆点让位 + 展开按钮.
export interface SidebarLayoutContext {
  sidebarOpen: boolean;
  showSidebar: () => void;
  inElectronShell: boolean;
}

export function AppLayout() {
  const { sid } = useParams();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpenRaw] = useState(initialSidebarOpen);
  const [sidebarWidth, setSidebarWidthRaw] = useState(initialSidebarWidth);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const { data: sessions, mutate } = useSWR("sessions", () =>
    api.listSessions(),
  );
  const { mutate: globalMutate } = useSWRConfig();
  const inElectronShell = isElectronShell();

  // 全局会话状态 SSE 长连 — 一份覆盖所有 session, 让 sidebar 实时显示 spinner /
  // 等审批 / idle. App 挂载时 open, 卸载关. 浏览器 EventSource 自动重连.
  // 收到 running event 时顺手 mutate("sessions") 让新会话立刻拉到 (这是 Fix A
  // 的辅助 — sentinel 让 fetch resolve, mutate 拉的列表才能看到新 session).
  const setStatus = useSessionStatusStore((s) => s.setStatus);
  const openStream = useSessionStatusStore((s) => s.openStream);
  const closeStream = useSessionStatusStore((s) => s.closeStream);
  useEffect(() => {
    openStream();
    return () => closeStream();
  }, [openStream, closeStream]);
  // running event 触发 sessions 列表重拉 — 让"新会话立刻出现"的兜底链路更稳:
  // 即使 EmptyPage 的 mutate("sessions") 因为 race / dedup 漏了, status SSE
  // 这边一旦看到 running 就会再调一次. 同 sid 重复 mutate 是廉价幂等操作.
  useEffect(() => {
    const unsub = useSessionStatusStore.subscribe((state, prevState) => {
      for (const [sid, status] of state.statuses) {
        if (status === "running" && prevState.statuses.get(sid) !== "running") {
          globalMutate("sessions");
          break;
        }
      }
    });
    return unsub;
  }, [globalMutate]);
  // setStatus 没在这里直接用, 但 EventSource onmessage 走 store 的 setStatus, 抓 ref
  // 防 dead code elimination (eslint 也别 warn 了).
  void setStatus;

  // client-side title 过滤. sessions 规模化后 (>200 条? 还要看真实) 再考虑加后端
  // GET /sessions?q=... 全文搜接口. 当前 LRU 8 + DB 历史不大, 拉全量足够.
  const filteredSessions = useMemo(() => {
    if (!searchOpen || !searchQuery.trim()) return sessions ?? [];
    const q = searchQuery.trim().toLowerCase();
    return (sessions ?? []).filter((s) =>
      (s.title ?? s.session_id).toLowerCase().includes(q),
    );
  }, [sessions, searchOpen, searchQuery]);

  const searching = searchOpen && searchQuery.trim().length > 0;
  const noMatches = searching && filteredSessions.length === 0;
  // offcanvas 折叠模式 — sidebar 整块隐藏 (width 0), 主区从左边缘起;
  // main 顶部加 floating 展开按钮替代折叠态 brand row.
  const sidebarDisplayWidth = sidebarOpen ? sidebarWidth : 0;

  function closeSearch() {
    setSearchOpen(false);
    setSearchQuery("");
  }

  function setSidebarOpen(next: boolean) {
    setSidebarOpenRaw(next);
    try {
      window.localStorage.setItem(SIDEBAR_OPEN_KEY, next ? "true" : "false");
    } catch {
      /* localStorage 不可用就算了 */
    }
  }

  function setSidebarWidth(next: number) {
    const width = clamp(Math.round(next), SIDEBAR_MIN, SIDEBAR_MAX);
    setSidebarWidthRaw(width);
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width));
    } catch {
      /* localStorage 不可用就算了 */
    }
  }

  function beginSidebarResize(e: ReactPointerEvent<HTMLDivElement>) {
    if (!sidebarOpen) return;
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = sidebarWidth;
    const previousCursor = document.body.style.cursor;
    const previousSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    function onMove(ev: PointerEvent) {
      const viewportWidth = window.innerWidth || SIDEBAR_MAX;
      const maxByMain = viewportWidth - RIGHT_PANEL_RESERVE - MAIN_CONTENT_MIN_WIDTH;
      const maxWidth = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, maxByMain));
      setSidebarWidth(clamp(startWidth + ev.clientX - startX, SIDEBAR_MIN, maxWidth));
    }
    function onUp() {
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousSelect;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp, { once: true });
  }

  return (
    <div className="weave-texture relative flex h-full w-full">
      {/* ── 侧栏 ─────────────────────────────────────────────── */}
      <aside
        className="relative flex h-full shrink-0 flex-col overflow-hidden transition-[width] duration-150"
        style={{ width: sidebarDisplayWidth }}
      >
        {/* brand 区 — Electron 下分两行:
              row 1: 48px 纯 drag spacer, macOS hiddenInset 三圆点漂在这里
              row 2: brand row (logo + title + 弹性 + 搜索 + 收起)
            非 Electron: 没三圆点, brand row 直接占顶部. */}
        {sidebarOpen ? (
          <>
            {inElectronShell && <div className="app-drag-region h-12" />}
            <div className="flex items-center gap-1 px-3 pb-2">
              <button
                type="button"
                onClick={() => navigate("/")}
                title="Back to start"
                className="flex min-w-0 flex-1 items-center gap-2 rounded-[6px] px-2 py-1.5 transition-colors hover:bg-[color:var(--color-bg-raised)]"
              >
                <LoomMark size={18} active={false} />
                <span className="font-display min-w-0 flex-1 truncate text-left text-[16px] font-medium leading-none tracking-[-0.006em] text-[color:var(--color-paper)]">
                  PentaLoom
                </span>
              </button>
              <button
                type="button"
                onClick={() => setSearchOpen(true)}
                title="Search threads"
                className="shrink-0 rounded-[5px] border border-transparent p-1.5 text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-line)] hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)]"
              >
                <Search size={14} />
              </button>
              <button
                type="button"
                onClick={() => setSidebarOpen(false)}
                title="Collapse sidebar"
                className="shrink-0 rounded-[5px] border border-transparent p-1.5 text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-line)] hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)]"
              >
                <PanelLeftClose size={13} />
              </button>
            </div>
          </>
        ) : null}

        {sidebarOpen && (
          <>
            {/* 新建会话按钮 — ghost 风, 不抢戏; hover 才显 bg, 跟下面分组标题节奏一致 */}
            <button
              type="button"
              onClick={() => navigate("/")}
              className="mx-2 mt-2 flex items-center gap-2 rounded-[6px] px-3 py-1.5 text-left text-[13px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
            >
              <SquarePen
                size={13}
                className="shrink-0 text-[color:var(--color-ink)]"
              />
              <span>New thread</span>
            </button>

            {/* 列表 */}
            <div className="scrollbar-hidden scroll-fade-y mt-3 flex-1 overflow-y-auto">
              {noMatches ? (
                <div className="px-5 py-10 text-center">
                  <p className="font-display text-[13px] italic text-[color:var(--color-paper-dim)]">
                    No threads match
                  </p>
                  <p className="mt-1 font-mono text-[11px] text-[color:var(--color-ink)]">
                    "{searchQuery.trim()}"
                  </p>
                </div>
              ) : (
                <SessionList
                  sessions={filteredSessions}
                  currentSid={sid}
                  onChanged={() => mutate()}
                />
              )}
            </div>

            {/* 底部 footer */}
            <div className="px-4 py-3">
              <button
                type="button"
                title="Settings"
                aria-label="Settings"
                className="rounded-[5px] p-1.5 text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
              >
                <Settings size={14} />
              </button>
            </div>
          </>
        )}

        {sidebarOpen && (
          <div
            role="separator"
            aria-orientation="vertical"
            title="Drag to resize sidebar"
            onPointerDown={beginSidebarResize}
            className="absolute right-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[color:var(--color-accent)]/20"
          />
        )}
      </aside>

      {/* ── 主区 ─────────────────────────────────────────────── */}
      <main className="relative flex-1 overflow-hidden">
        {inElectronShell && <div className="app-window-drag-strip" />}
        <Outlet
          context={{
            sidebarOpen,
            showSidebar: () => setSidebarOpen(true),
            inElectronShell,
          }}
        />
      </main>

      {searchOpen && (
        <div className="app-no-drag absolute inset-0 z-50 bg-[rgba(247,246,243,0.58)] backdrop-blur-[7px]">
          <button
            type="button"
            aria-label="Close search"
            onClick={closeSearch}
            className="absolute inset-0 cursor-default"
          />
          <div className="relative mx-auto mt-[22vh] w-[min(640px,calc(100vw-48px))] overflow-hidden rounded-[16px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)]/92 shadow-[0_24px_80px_rgba(20,30,50,0.18)] ring-1 ring-white/70">
            <div className="flex items-center gap-3 px-4 py-3">
              <Search size={18} className="shrink-0 text-[color:var(--color-ink)]" />
              <input
                autoFocus
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.preventDefault();
                    closeSearch();
                  }
                }}
                placeholder="Search threads…"
                className="min-w-0 flex-1 bg-transparent text-[15px] text-[color:var(--color-paper)] placeholder:text-[color:var(--color-ink-dim)] focus:outline-none"
              />
              <button
                type="button"
                onClick={closeSearch}
                title="Close search (Esc)"
                className="shrink-0 rounded-[6px] p-1.5 text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
              >
                <X size={15} />
              </button>
            </div>

            <div className="max-h-[360px] overflow-y-auto border-t border-[color:var(--color-line-soft)] px-2 py-2">
              {filteredSessions.length === 0 ? (
                <div className="px-3 py-10 text-center">
                  <p className="font-display text-[13px] italic text-[color:var(--color-paper-dim)]">
                    No threads found
                  </p>
                  <p className="mt-1 font-mono text-[11px] text-[color:var(--color-ink)]">
                    {searchQuery.trim() ? `“${searchQuery.trim()}”` : "Start typing to search"}
                  </p>
                </div>
              ) : (
                <ul className="space-y-1">
                  {filteredSessions.slice(0, 8).map((session, index) => {
                    const title = session.title ?? shortenSid(session.session_id);
                    return (
                      <li key={session.session_id}>
                        <button
                          type="button"
                          onClick={() => {
                            navigate(`/s/${session.session_id}`);
                            closeSearch();
                          }}
                          className="group flex w-full items-center gap-3 rounded-[10px] px-3 py-2.5 text-left transition-colors hover:bg-[color:var(--color-bg-raised)] data-[active=true]:bg-[color:var(--color-bg-raised)]"
                          data-active={index === 0 ? "true" : undefined}
                        >
                          <GitBranch size={14} className="shrink-0 text-[color:var(--color-thread-file)] opacity-60" />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[13px] font-medium text-[color:var(--color-paper)]">
                              {title}
                            </div>
                            <div className="mt-0.5 truncate text-[11px] text-[color:var(--color-ink)]">
                              {session.mounted_dirs.length} mount{session.mounted_dirs.length === 1 ? "" : "s"}
                            </div>
                          </div>
                          <span className="tabular shrink-0 font-mono text-[10.5px] text-[color:var(--color-ink)]">
                            {formatRelative(session.last_active_at)}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
