import { useMemo, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Outlet, useNavigate, useParams } from "react-router";
import { PanelLeftClose, Search, SquarePen, X } from "lucide-react";
import useSWR from "swr";

import { SessionList } from "@/components/sidebar/SessionList";
import { LoomMark } from "@/components/brand/LoomMark";
import { api } from "@/lib/api";
import { MAIN_CONTENT_MIN_WIDTH } from "@/lib/layout-constraints";

const SIDEBAR_OPEN_KEY = "pentaloom:left-sidebar:open";
const SIDEBAR_WIDTH_KEY = "pentaloom:left-sidebar:width";
const SIDEBAR_MIN = 220;
const SIDEBAR_DEFAULT = 260;
const SIDEBAR_MAX = 420;
const SIDEBAR_COLLAPSED = 56;
const RIGHT_PANEL_RESERVE = 340;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function initialSidebarOpen(): boolean {
  if (typeof window === "undefined") return true;
  return window.localStorage.getItem(SIDEBAR_OPEN_KEY) !== "false";
}

function initialSidebarWidth(): number {
  if (typeof window === "undefined") return SIDEBAR_DEFAULT;
  const saved = Number(window.localStorage.getItem(SIDEBAR_WIDTH_KEY));
  return Number.isFinite(saved) ? clamp(saved, SIDEBAR_MIN, SIDEBAR_MAX) : SIDEBAR_DEFAULT;
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
    <div className="flex h-full w-full">
      {/* ── 侧栏 ─────────────────────────────────────────────── */}
      <aside
        className="relative flex h-full shrink-0 flex-col border-r border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] transition-[width] duration-150"
        style={{ width: sidebarOpen ? sidebarWidth : SIDEBAR_COLLAPSED }}
      >
        {/* brand — 展开态: 整块 LoomMark + 标题点回首页, 右侧独立折叠按钮.
            折叠态: 只 LoomMark, 点它 = 展开 sidebar (替代展开按钮职责, 避免两个 icon 挤). */}
        {sidebarOpen ? (
          <div className="flex items-center gap-1 px-3 py-4">
            <button
              type="button"
              onClick={() => navigate("/")}
              title="Back to start"
              className="flex min-w-0 flex-1 items-center gap-2.5 rounded-[6px] px-2 py-1 transition-colors hover:bg-[color:var(--color-bg-raised)]"
            >
              <LoomMark size={22} active={false} />
              <span className="font-display min-w-0 flex-1 text-left text-[18px] font-medium tracking-[-0.01em] text-[color:var(--color-paper)]">
                PentaLoom
              </span>
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
        ) : (
          <div className="flex justify-center px-2 py-4">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              title="Expand sidebar"
              className="rounded-[6px] p-1.5 transition-colors hover:bg-[color:var(--color-bg-raised)]"
            >
              <LoomMark size={22} active={false} />
            </button>
          </div>
        )}

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

            {/* 搜索按钮 / 展开后变 input — 跟 New thread 同款 ghost, 上下排.
                client-side 过滤 title, sessions 规模化后再加后端 q 参数. */}
            {searchOpen ? (
              <div className="mx-2 mt-px flex items-center gap-2 rounded-[6px] bg-[color:var(--color-bg-raised)] px-3 py-1.5">
                <Search
                  size={13}
                  className="shrink-0 text-[color:var(--color-ink)]"
                />
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
                  className="min-w-0 flex-1 bg-transparent text-[13px] text-[color:var(--color-paper)] placeholder:text-[color:var(--color-ink-dim)] focus:outline-none"
                />
                <button
                  type="button"
                  onClick={closeSearch}
                  title="Close search (Esc)"
                  className="shrink-0 rounded-[3px] p-0.5 text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)]"
                >
                  <X size={11} />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setSearchOpen(true)}
                className="mx-2 mt-px flex items-center gap-2 rounded-[6px] px-3 py-1.5 text-left text-[13px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
              >
                <Search
                  size={13}
                  className="shrink-0 text-[color:var(--color-ink)]"
                />
                <span>Search threads</span>
              </button>
            )}

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
            <div className="px-5 py-3">
              <div className="font-mono text-[10px] tracking-wider text-[color:var(--color-ink-dim)]">
                v0.1.0
              </div>
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
      <main className="relative flex-1 overflow-hidden bg-[color:var(--color-bg)]">
        <Outlet />
      </main>
    </div>
  );
}
