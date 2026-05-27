import { Outlet, useNavigate, useParams } from "react-router";
import useSWR from "swr";

import { SessionList } from "@/components/sidebar/SessionList";
import { LoomMark } from "@/components/brand/LoomMark";
import { api } from "@/lib/api";

export function AppLayout() {
  const { sid } = useParams();
  const navigate = useNavigate();
  const { data: sessions, mutate } = useSWR("sessions", () =>
    api.listSessions(),
  );

  return (
    <div className="flex h-full w-full">
      {/* ── 侧栏 ─────────────────────────────────────────────── */}
      <aside className="flex h-full w-[260px] flex-col border-r border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)]">
        {/* brand */}
        <div className="flex items-center gap-2.5 px-5 py-4">
          <LoomMark size={22} active={false} />
          <div className="text-[15px] font-semibold tracking-tight text-[color:var(--color-paper)]">
            PentaLoom
          </div>
        </div>

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
        <div className="mt-3 flex-1 overflow-y-auto">
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
      </aside>

      {/* ── 主区 ─────────────────────────────────────────────── */}
      <main className="relative flex-1 overflow-hidden bg-[color:var(--color-bg)]">
        <Outlet />
      </main>
    </div>
  );
}
