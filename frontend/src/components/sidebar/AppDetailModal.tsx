// AppDetailModal — 点 sidebar Apps 行弹出, 看 weaver app 详情.
//
// 设计原则:
//   - 数据一次拉 (api.getAppDetail), 在 modal 里 useSWR 缓存
//   - 不自己做源码 viewer — 文件名是按钮, 点击调 api.openFile 用系统默认 app 打开
//     (跟 Workspace Open in Finder 一脉相承; macOS `open file.py` 走用户配的默认)
//   - 不含手动 invoke 表单 (Phase B.6 不做; 留给 Phase C 跟 window runtime 一起设计)
//   - 4 个 section: Header (name + status + description) / Invocations / Files / Recent runs
//
// caveat: 用户在 VSCode 改 weaver/apps/<name>/files/handler.py 不会触发状态机
// 打回 dirty (状态机只跟踪 meta-tool 调用). 这是 Phase B.5 固有限制, 改完用户得
// 自己跟 agent 说 "重 finalize" 才能让 invoke 跑新代码.

import { useEffect } from "react";
import { createPortal } from "react-dom";
import useSWR from "swr";
import { AppWindow, Clock, ExternalLink, Loader2, X } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type {
  AppDetailResponse,
  AppFileEntry,
  AppInvocationSummary,
  AppRunLog,
  AppStatus,
} from "@/lib/types";
import { iconForExt } from "@/lib/tool-meta";
import { cn } from "@/lib/utils";

// Electron preload 在 window.__PENTALOOM__ 上挂的 API; web 模式下整个对象不存在.
// 见 electron/src/preload/preload.ts.
declare global {
  interface Window {
    __PENTALOOM__?: {
      apiBase: string;
      openAppWindow?: (payload: {
        name: string;
        entry?: string;
        title?: string;
        width?: number;
        height?: number;
      }) => Promise<{ name: string; reused: boolean }>;
    };
  }
}

interface Props {
  appName: string;
  sessionId: string;  // 给 openFile 用 — 文件路径在 weaver/ 下, 跨 session 通用, 但 API 需要 sid
  onClose: () => void;
}

const statusBadge: Record<AppStatus, { label: string; cls: string }> = {
  draft:  { label: "draft",  cls: "text-[color:var(--color-ink-dim)] bg-[color:var(--color-bg-raised)]" },
  ready:  { label: "ready",  cls: "text-[#2d5a3d] bg-[#d4ead9]" },
  dirty:  { label: "dirty",  cls: "text-[#6b5400] bg-[#f4e4a0]" },
  failed: { label: "failed", cls: "text-[#7a2d2d] bg-[#f0c8c8]" },
};

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function AppDetailModal({ appName, sessionId, onClose }: Props) {
  const { data, error, isLoading } = useSWR<AppDetailResponse>(
    appName ? `app-detail:${appName}` : null,
    () => api.getAppDetail(appName),
    { revalidateOnFocus: false },
  );

  // ESC 关
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function openSource(file: AppFileEntry) {
    if (!sessionId) {
      toast.error("Open file requires an active session");
      return;
    }
    try {
      await api.openFile({ sessionId, path: file.absolute_path });
    } catch (e) {
      toast.error(`Open failed: ${String(e)}`);
    }
  }

  // 是否能打开 app window: 必须 electron shell + status=ready + app.json 有 window component.
  // window 名字直接用 app name (electron main 一名一窗复用).
  const electronApi = typeof window !== "undefined" ? window.__PENTALOOM__ : undefined;
  const canOpenWindow =
    !!electronApi?.openAppWindow &&
    data?.meta?.status === "ready" &&
    (data.summary.components?.windows ?? []).length > 0;

  async function openAppWindow() {
    if (!canOpenWindow || !electronApi?.openAppWindow) return;
    try {
      const r = await electronApi.openAppWindow({ name: appName });
      if (r.reused) {
        toast.info(`Window already open — focused`);
      }
    } catch (e) {
      toast.error(`Open window failed: ${String(e)}`);
    }
  }

  // 用 portal 挂到 document.body, 防 sidebar 任何祖先的 stacking context (transform /
  // overflow / contain) 把 fixed inset-0 限制在 sidebar 区域 — 之前直接渲染就遇到了
  // backdrop 只盖 sidebar 不盖主区的视觉 bug.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex h-[min(680px,85vh)] w-[min(720px,92vw)] flex-col overflow-hidden rounded-[10px] border border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-card)] shadow-[0_20px_60px_-15px_rgba(20,30,50,0.18)]">
        {/* Header */}
        <div className="flex items-start gap-3 border-b border-[color:var(--color-line-soft)] px-5 pt-4 pb-3">
          <AppWindow size={18} className="mt-0.5 shrink-0 text-[color:var(--color-thread-file)]" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="font-display text-[16px] italic text-[color:var(--color-paper)]">
                {appName}
              </h2>
              {data?.meta && (
                <span
                  className={cn(
                    "tabular shrink-0 rounded-[3px] px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-wider",
                    statusBadge[data.meta.status].cls,
                  )}
                >
                  {statusBadge[data.meta.status].label}
                </span>
              )}
            </div>
            {data?.meta && (
              <p className="mt-0.5 truncate text-[11.5px] text-[color:var(--color-ink)]">
                {data.meta.description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={openAppWindow}
            disabled={!canOpenWindow}
            title={
              canOpenWindow
                ? "Open app window"
                : !electronApi?.openAppWindow
                  ? "Open requires Electron shell"
                  : data?.meta?.status !== "ready"
                    ? `Status is ${data?.meta?.status ?? "unknown"} — finalize first`
                    : "App has no window component"
            }
            className={cn(
              "flex shrink-0 items-center gap-1 rounded-[5px] border px-2 py-1 text-[11px] transition-colors",
              canOpenWindow
                ? "border-[color:var(--color-line)] text-[color:var(--color-paper-dim)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
                : "cursor-not-allowed border-[color:var(--color-line-soft)] text-[color:var(--color-ink-dim)] opacity-60",
            )}
          >
            <ExternalLink size={11} />
            Open
          </button>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-[5px] p-1 text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
            title="Close (Esc)"
          >
            <X size={14} />
          </button>
        </div>

        {/* 主体 — 滚动 */}
        <div className="scrollbar-hidden min-h-0 flex-1 overflow-y-auto px-5 py-3">
          {isLoading && (
            <div className="flex items-center gap-2 px-1 py-8 text-[color:var(--color-ink)]">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-[12px] italic">Loading detail…</span>
            </div>
          )}
          {error && (
            <div className="rounded-[6px] border border-[#f0c8c8] bg-[#f8e8e8] px-3 py-2 text-[12px] text-[#7a2d2d]">
              加载失败: {String(error)}
            </div>
          )}

          {data && (
            <div className="space-y-5">
              {/* finalize 错误提示 — failed 状态下高亮显示 */}
              {data.meta?.status === "failed" && data.meta.last_finalize_error && (
                <Section title="Finalize error">
                  <pre className="whitespace-pre-wrap break-words rounded-[4px] bg-[#f8e8e8] px-2 py-1.5 font-mono text-[11px] text-[#7a2d2d]">
                    {data.meta.last_finalize_error}
                  </pre>
                </Section>
              )}

              {/* Invocations */}
              <Section
                title="Invocations"
                count={data.summary.invocations.length}
              >
                {data.summary.invocations.length === 0 ? (
                  <Placeholder>No invocations.</Placeholder>
                ) : (
                  <ul className="space-y-1.5">
                    {data.summary.invocations.map((inv) => (
                      <InvocationRow key={inv.id} inv={inv} />
                    ))}
                  </ul>
                )}
              </Section>

              {/* Files */}
              <Section title="Files" count={data.files.length}>
                {data.files.length === 0 ? (
                  <Placeholder>No source files (skeleton only).</Placeholder>
                ) : (
                  <ul className="space-y-0.5">
                    {data.files.map((f) => (
                      <FileRow key={f.rel_path} file={f} onOpen={openSource} />
                    ))}
                  </ul>
                )}
                {data.files.length > 0 && (
                  <p className="mt-1.5 px-1 text-[10.5px] italic text-[color:var(--color-ink)]">
                    点击文件用系统默认编辑器打开. 用户改完需让 agent 重 finalize 才能 invoke 跑新代码.
                  </p>
                )}
              </Section>

              {/* Recent runs */}
              <Section title="Recent runs" count={data.recent_runs.length}>
                {data.recent_runs.length === 0 ? (
                  <Placeholder>No runs yet.</Placeholder>
                ) : (
                  <ul className="space-y-0.5">
                    {data.recent_runs.slice().reverse().map((r, i) => (
                      <RunRow key={`${r.run_id}-${i}`} run={r} />
                    ))}
                  </ul>
                )}
              </Section>

              {/* meta footer */}
              {data.meta && (
                <Section title="Meta">
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
                    <MetaField label="created" value={fmtTime(data.meta.created_at)} />
                    <MetaField label="updated" value={fmtTime(data.meta.updated_at)} />
                    <MetaField
                      label="last finalized"
                      value={data.meta.last_finalized_at ? fmtTime(data.meta.last_finalized_at) : "—"}
                    />
                    <MetaField
                      label="last used"
                      value={data.meta.last_used_at ? fmtTime(data.meta.last_used_at) : "—"}
                    />
                    <MetaField label="use count" value={String(data.meta.use_count)} />
                    <MetaField label="source" value={data.meta.source} />
                    <MetaField label="trusted" value={data.meta.is_trusted ? "yes" : "no"} />
                  </dl>
                </Section>
              )}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ─── 内部小组件 ─────────────────────────────────────────────

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">
          {title}
        </span>
        {count !== undefined && (
          <span className="tabular font-mono text-[10px] text-[color:var(--color-ink-dim)]">
            · {count}
          </span>
        )}
      </div>
      {children}
    </section>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-1 py-1.5 font-display text-[11.5px] italic text-[color:var(--color-ink)]">
      {children}
    </div>
  );
}

function InvocationRow({ inv }: { inv: AppInvocationSummary }) {
  const targetText = inv.target
    ? `${inv.target.component}/${inv.target.name}`
    : "—";
  return (
    <li className="rounded-[4px] border border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-soft)] px-2.5 py-1.5">
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-[12px] text-[color:var(--color-paper)]">
          {inv.id}
        </span>
        <span className="tabular font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          → {targetText}
        </span>
      </div>
      <div className="mt-0.5 text-[11px] text-[color:var(--color-ink)]">
        {inv.description || "(no description)"}
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
        <span>input: {inv.input_keys.length > 0 ? inv.input_keys.join(", ") : "—"}</span>
        <span>output: {inv.output_keys.length > 0 ? inv.output_keys.join(", ") : "—"}</span>
        <span>timeout: {fmtMs(inv.timeout_ms)}</span>
      </div>
    </li>
  );
}

function FileRow({
  file,
  onOpen,
}: {
  file: AppFileEntry;
  onOpen: (file: AppFileEntry) => void;
}) {
  const Icon = iconForExt(file.ext);
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(file)}
        title={`Open ${file.absolute_path}`}
        className="group flex w-full items-center gap-2 rounded-[4px] px-1.5 py-1 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]"
      >
        <Icon size={12} className="shrink-0 text-[color:var(--color-thread-file)]" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)] group-hover:text-[color:var(--color-paper)]">
          {file.rel_path}
        </span>
        {file.ext && (
          <span className="tabular shrink-0 font-mono text-[9.5px] uppercase text-[color:var(--color-ink-dim)]">
            {file.ext}
          </span>
        )}
        <span className="tabular shrink-0 font-mono text-[9.5px] text-[color:var(--color-ink-dim)]">
          {fmtBytes(file.size)}
        </span>
      </button>
    </li>
  );
}

function RunRow({ run }: { run: AppRunLog }) {
  const isOk = run.status === "success";
  return (
    <li
      title={run.error || run.run_id}
      className="flex items-center gap-2 rounded-[4px] px-1.5 py-1 hover:bg-[color:var(--color-bg-raised)]"
    >
      <Clock size={11} className="shrink-0 text-[color:var(--color-ink-dim)]" />
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[color:var(--color-paper-dim)]">
        {run.invocation_id}
      </span>
      <span
        className={cn(
          "tabular shrink-0 font-mono text-[10px] uppercase tracking-wider",
          isOk ? "text-[#2d5a3d]" : "text-[#7a2d2d]",
        )}
      >
        {run.status}
      </span>
      <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
        {fmtMs(run.duration_ms)}
      </span>
    </li>
  );
}

function MetaField({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="font-mono text-[color:var(--color-ink-dim)]">{label}</dt>
      <dd className="font-mono text-[color:var(--color-paper-dim)]">{value}</dd>
    </>
  );
}
