import { useState, type PointerEvent as ReactPointerEvent } from "react";
import { Outlet, useNavigate, useParams } from "react-router";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import useSWR from "swr";

import { SessionList } from "@/components/sidebar/SessionList";
import { LoomMark } from "@/components/brand/LoomMark";
import { api } from "@/lib/api";
import { MAIN_CONTENT_MIN_WIDTH } from "@/lib/layout-constraints";
import { cn } from "@/lib/utils";

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
  const { data: sessions, mutate } = useSWR("sessions", () =>
    api.listSessions(),
  );

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
        {/* brand */}
        <div
          className={cn(
            "flex items-center gap-2.5 py-4",
            sidebarOpen ? "px-5" : "justify-center px-2",
          )}
        >
          <LoomMark size={22} active={false} />
          {sidebarOpen && (
            <div className="font-display min-w-0 flex-1 text-[18px] font-medium tracking-[-0.01em] text-[color:var(--color-paper)]">
              PentaLoom
            </div>
          )}
          <button
            type="button"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
            className="shrink-0 rounded-[5px] border border-transparent p-1.5 text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-line)] hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)]"
          >
            {sidebarOpen ? <PanelLeftClose size={13} /> : <PanelLeftOpen size={13} />}
          </button>
        </div>

        {sidebarOpen && (
          <>
            {/* 新建会话按钮 */}
            <button
              type="button"
              onClick={() => navigate("/")}
              className="mx-3 mt-1 flex items-center justify-between rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-left text-[13px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:text-[color:var(--color-paper)]"
            >
              <span>New thread</span>
              <span className="text-[15px] leading-none text-[color:var(--color-ink)]">
                +
              </span>
            </button>

            {/* 列表 */}
            <div className="scrollbar-hidden scroll-fade-y mt-3 flex-1 overflow-y-auto">
              <SessionList
                sessions={sessions ?? []}
                currentSid={sid}
                onChanged={() => mutate()}
              />
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
