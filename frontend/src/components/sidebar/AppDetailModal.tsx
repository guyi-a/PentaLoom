// AppDetailModal — 点 sidebar Apps 行弹出, 看 weaver app 详情.
//
// 设计原则:
//   - 数据一次拉 (api.getAppDetail), 在 modal 里 useSWR 缓存
//   - 不自己做源码 viewer — 文件名是按钮, 点击调 api.openFile 用系统默认 app 打开
//     (跟 Workspace Open in Finder 一脉相承; macOS `open file.py` 走用户配的默认)
//   - 不含手动 invoke 表单 (留给 window runtime 一起设计)
//   - 4 个 section: Header (name + status + description) / Invocations / Files / Recent runs
//
// caveat: 用户在 VSCode 改 weaver/apps/<name>/files/handler.py 不会触发状态机
// 打回 dirty (状态机只跟踪 meta-tool 调用). 这是固有限制, 改完用户得
// 自己跟 agent 说 "重 finalize" 才能让 invoke 跑新代码.

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import useSWR from "swr";
import { AppWindow, CalendarClock, ChevronDown, ChevronRight, ExternalLink, Eye, Folder, GitBranch, Loader2, RefreshCw, Server, Square, User, X } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type {
  AppDetailResponse,
  AppEphemeralService,
  AppFileEntry,
  AppInvocationSummary,
  AppRunLog,
  AppRunningService,
  AppScheduleTrigger,
  AppStatus,
  AppWatchEntry,
  AppWatchFilesResponse,
  AppWatchTrigger,
} from "@/lib/types";
import { iconForExt } from "@/lib/tool-meta";
import type { PreviewFile } from "@/lib/preview-store";
import { cn } from "@/lib/utils";

import { FilePreview } from "@/components/right-panel/file-preview/FilePreview";

// Electron preload 在 window.__PENTALOOM__ 上挂的 API; web 模式下整个对象不存在.
// 见 electron/src/preload/preload.ts.
declare global {
  interface Window {
    __PENTALOOM__?: {
      apiBase: string;
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
  const [previewFile, setPreviewFile] = useState<PreviewFile | null>(null);
  const { data, error, isLoading, mutate, isValidating } = useSWR<AppDetailResponse>(
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

  // 点击 Files 树的文件 → 切换内嵌预览 (复用右栏 FilePreview).
  // 同一个文件再次点击 → 收起预览. file.path 是后端鉴权 key.
  function previewSource(file: AppFileEntry) {
    setPreviewFile((cur) => {
      if (cur?.path === file.absolute_path) return null;
      const parts = file.rel_path.split("/").filter(Boolean);
      return {
        path: file.absolute_path,
        name: parts[parts.length - 1] ?? file.rel_path,
        relativePath: file.rel_path,
      };
    });
  }

  // 能打开 window 的条件: status=ready + app.json 有 window component.
  // 不再依赖 electron 主壳 IPC — 走 fetch POST /weaver/apps/{name}/window/open,
  // 后端通过 loom Unix socket spawn loomer 子进程渲窗.
  const canOpenWindow =
    data?.meta?.status === "ready" &&
    (data.summary.components?.windows ?? []).length > 0;

  async function openAppWindow() {
    if (!canOpenWindow) return;
    try {
      const r = await api.openAppWindow(appName);
      toast.success(`Window opened (pid=${r.pid})`);
    } catch (e) {
      toast.error(`Open window failed: ${String(e)}`);
    }
  }

  // 用 portal 挂到 document.body, 防 sidebar 任何祖先的 stacking context (transform /
  // overflow / contain) 把 fixed inset-0 限制在 sidebar 区域 — 之前直接渲染就遇到了
  // backdrop 只盖 sidebar 不盖主区的视觉 bug.
  //
  // 书页布局: previewFile 时 modal 展宽成左右两栏 — 左栏 app 详情, 右栏文件预览.
  // transition width + opacity 让展开/收起有动感.
  const hasPreview = previewFile !== null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={cn(
          "flex flex-col overflow-hidden rounded-[10px] border border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-card)] shadow-[0_20px_60px_-15px_rgba(20,30,50,0.18)] transition-[width] duration-300 ease-in-out",
          hasPreview
            ? "h-[min(680px,90vh)] w-[min(1160px,95vw)]"
            : "h-[min(680px,85vh)] w-[min(720px,92vw)]",
        )}
      >
        {/* 书页双栏: 有预览时左右分栏, 否则单栏 */}
        <div className="flex min-h-0 flex-1 overflow-hidden">
          {/* 左栏 — app 详情 (始终存在) */}
          <div
            className={cn(
              "flex flex-col overflow-hidden transition-[width] duration-300 ease-in-out",
              hasPreview ? "w-[340px] shrink-0" : "w-full",
            )}
          >
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
                onClick={() => mutate()}
                disabled={isValidating}
                title="Refresh"
                className="shrink-0 rounded-[5px] p-1 text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)] disabled:cursor-not-allowed disabled:opacity-40"
              >
                <RefreshCw size={13} className={isValidating ? "animate-spin" : ""} />
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
            <div className="scrollbar-hidden min-h-0 flex-1 overflow-y-auto px-5 py-3" style={{ scrollbarWidth: 'none' }}>
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
                <Section title="Finalize error" defaultOpen>
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
                  <FileTree
                    files={data.files}
                    selectedPath={previewFile?.path ?? null}
                    onPreview={previewSource}
                  />
                )}
                {data.files.length > 0 && (
                  <p className="mt-1.5 px-1 text-[10.5px] italic text-[color:var(--color-ink)]">
                    点文件在右侧预览, 预览头按钮用系统默认编辑器打开.
                  </p>
                )}
              </Section>

              {/* D-4: Services (declared = launchd 监督, 跨重启常驻) */}
              {(data.running_services?.length ?? 0) > 0 && (
                <Section title="Services" count={data.running_services?.length}>
                  <ul className="space-y-0.5">
                    {(data.running_services ?? []).map((s) => (
                      <ServiceRow
                        key={s.name}
                        appName={appName}
                        svc={s}
                        onStopped={() => mutate()}
                      />
                    ))}
                  </ul>
                  <p className="mt-1.5 px-1 text-[10.5px] italic text-[color:var(--color-ink)]">
                    Lazy spawn — agent / window 调 invoke_app 时自动起. ✕ 停掉释放进程, 下次 invoke 自动重起.
                  </p>
                </Section>
              )}

              {/* Ephemeral services — agent 织造期临时跑的, memory-only,
                  PentaLoom 重启全清. 跟 declared services 并列展示. */}
              {(data.ephemeral_services?.length ?? 0) > 0 && (
                <Section
                  title="Ephemeral services"
                  count={data.ephemeral_services?.length}
                >
                  <ul className="space-y-0.5">
                    {(data.ephemeral_services ?? []).map((s) => (
                      <EphemeralServiceRow
                        key={s.service_name}
                        svc={s}
                      />
                    ))}
                  </ul>
                  <p className="mt-1.5 px-1 text-[10.5px] italic text-[color:var(--color-ink)]">
                    织造期 ephemeral (agent weave_service_start 起的). PentaLoom 重启全清. finalize 后 declared launchd 接管.
                  </p>
                </Section>
              )}

              {/* Schedules section — cron 触发 + 状态 snapshot */}
              {(data.triggers?.schedules?.length ?? 0) > 0 && (
                <Section title="Schedules" count={data.triggers?.schedules?.length}>
                  <ul className="space-y-0.5">
                    {(data.triggers?.schedules ?? []).map((s) => (
                      <ScheduleRow key={s.name} sched={s} />
                    ))}
                  </ul>
                  <p className="mt-1.5 px-1 text-[10.5px] italic text-[color:var(--color-ink)]">
                    Cron-driven invocation. Overlap (上次还在跑) 自动 skip — modal Recent runs 可见.
                  </p>
                </Section>
              )}

              {/* E (watch): components.watches[] lazy-fetch 每个 watch 的文件清单 */}
              {(data.summary.components?.watches ?? []).length > 0 && (
                <Section title="Watches" count={data.summary.components?.watches?.length}>
                  <ul className="space-y-1">
                    {(data.summary.components?.watches ?? []).map((wname) => {
                      // 找这个 watch 对应的 trigger 状态 (有 invocation_id 才有)
                      const trig = (data.triggers?.watches ?? []).find(
                        (t) => t.name === wname,
                      );
                      return (
                        <WatchRow
                          key={wname}
                          appName={appName}
                          watchName={wname}
                          sessionId={sessionId}
                          trigger={trig}
                        />
                      );
                    })}
                  </ul>
                </Section>
              )}

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
            </div>{/* 主体滚动区 end */}
          </div>{/* 左栏 end */}

          {/* 右栏 — 文件预览 (有 previewFile 时出现) */}
          {hasPreview && previewFile && (
            <div className="flex min-w-0 flex-1 flex-col border-l border-[color:var(--color-line-soft)]">
              <FilePreview
                file={previewFile}
                sessionId={sessionId}
                onClose={() => setPreviewFile(null)}
              />
            </div>
          )}
        </div>{/* 书页双栏 end */}
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
  defaultOpen = false,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="mb-1.5 flex w-full items-center gap-1.5 text-left hover:opacity-80"
      >
        {open ? (
          <ChevronDown size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        ) : (
          <ChevronRight size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        )}
        <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">
          {title}
        </span>
        {count !== undefined && (
          <span className="tabular font-mono text-[10px] text-[color:var(--color-ink-dim)]">
            · {count}
          </span>
        )}
      </button>
      {open && children}
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

type FileTreeNode = {
  name: string;
  path: string;
  children: Map<string, FileTreeNode>;
  file?: AppFileEntry;
};

function buildFileTree(files: AppFileEntry[]): FileTreeNode {
  const root: FileTreeNode = { name: "", path: "", children: new Map() };
  for (const file of files) {
    const parts = file.rel_path.split("/").filter(Boolean);
    let current = root;
    parts.forEach((part, index) => {
      const path = parts.slice(0, index + 1).join("/");
      let node = current.children.get(part);
      if (!node) {
        node = { name: part, path, children: new Map() };
        current.children.set(part, node);
      }
      if (index === parts.length - 1) node.file = file;
      current = node;
    });
  }
  return root;
}

function sortFileNodes(nodes: Iterable<FileTreeNode>): FileTreeNode[] {
  return Array.from(nodes).sort((a, b) => {
    const aIsDir = !a.file || a.children.size > 0;
    const bIsDir = !b.file || b.children.size > 0;
    if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

function FileTree({
  files,
  selectedPath,
  onPreview,
}: {
  files: AppFileEntry[];
  selectedPath: string | null;
  onPreview: (file: AppFileEntry) => void;
}) {
  const root = useMemo(() => buildFileTree(files), [files]);
  return (
    <ul className="space-y-0.5">
      {sortFileNodes(root.children.values()).map((node) => (
        <FileTreeRow
          key={node.path}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          onPreview={onPreview}
        />
      ))}
    </ul>
  );
}

function FileTreeRow({
  node,
  depth,
  selectedPath,
  onPreview,
}: {
  node: FileTreeNode;
  depth: number;
  selectedPath: string | null;
  onPreview: (file: AppFileEntry) => void;
}) {
  const isDir = node.children.size > 0;
  const [open, setOpen] = useState(depth === 0 && !node.name.startsWith("."));

  if (!isDir && node.file) {
    return (
      <FileRow
        file={node.file}
        label={node.name}
        depth={depth}
        selected={selectedPath === node.file.absolute_path}
        onPreview={onPreview}
      />
    );
  }

  const children = sortFileNodes(node.children.values());
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={node.path}
        className="group flex w-full items-center gap-1.5 rounded-[4px] py-1 pr-1.5 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]"
        style={{ paddingLeft: `${depth * 14 + 6}px` }}
      >
        {open ? (
          <ChevronDown size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        ) : (
          <ChevronRight size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        )}
        <Folder size={12} className="shrink-0 text-[color:var(--color-thread-file)]" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)] group-hover:text-[color:var(--color-paper)]">
          {node.name}
        </span>
        <span className="tabular shrink-0 font-mono text-[9.5px] text-[color:var(--color-ink-dim)]">
          {children.length}
        </span>
      </button>
      {open && (
        <ul className="space-y-0.5">
          {children.map((child) => (
            <FileTreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedPath={selectedPath}
              onPreview={onPreview}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function FileRow({
  file,
  label = file.rel_path,
  depth = 0,
  selected = false,
  onPreview,
}: {
  file: AppFileEntry;
  label?: string;
  depth?: number;
  selected?: boolean;
  onPreview: (file: AppFileEntry) => void;
}) {
  const Icon = iconForExt(file.ext);
  return (
    <li>
      <button
        type="button"
        onClick={() => onPreview(file)}
        title={`Preview ${file.absolute_path}`}
        className={cn(
          "group flex w-full items-center gap-2 rounded-[4px] py-1 pr-1.5 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]",
          selected && "bg-[color:var(--color-bg-raised)]",
        )}
        style={{ paddingLeft: `${depth * 14 + 6}px` }}
      >
        <Icon size={12} className="shrink-0 text-[color:var(--color-thread-file)]" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)] group-hover:text-[color:var(--color-paper)]">
          {label}
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
  const trigger = run.trigger ?? "user";
  // 三色 status. skipped 灰 — overlap / status race 不是真失败.
  const statusCls =
    run.status === "success"
      ? "text-[#2d5a3d]"
      : run.status === "skipped"
        ? "text-[color:var(--color-ink-dim)]"
        : "text-[#7a2d2d]";
  // trigger 来源 icon: user 默认/历史 entry, schedule 时钟, watch 眼睛, workflow 分支
  const TriggerIcon =
    trigger === "schedule"
      ? CalendarClock
      : trigger === "watch"
        ? Eye
        : trigger === "workflow"
          ? GitBranch
          : User;
  const triggerTitle =
    trigger === "schedule"
      ? "schedule trigger"
      : trigger === "watch"
        ? "watch trigger"
        : trigger === "workflow"
          ? "workflow step (invoke_app from workflow runtime)"
          : "user / agent invoke";
  return (
    <li
      title={`${triggerTitle}\n${run.error || run.run_id}`}
      className="flex items-center gap-2 rounded-[4px] px-1.5 py-1 hover:bg-[color:var(--color-bg-raised)]"
    >
      <TriggerIcon
        size={11}
        className="shrink-0 text-[color:var(--color-ink-dim)]"
      />
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[color:var(--color-paper-dim)]">
        {run.invocation_id}
      </span>
      <span
        className={cn(
          "tabular shrink-0 font-mono text-[10px] uppercase tracking-wider",
          statusCls,
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

// 时长格式: 给 service uptime 用. unix ts → 人可读相对时间.
function fmtUptime(startedAtSec: number): string {
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - startedAtSec));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) {
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    return m === 0 ? `${h}h` : `${h}h${m}m`;
  }
  return `${Math.floor(secs / 86400)}d`;
}

// 相对时间格式: 给 ScheduleRow next_fire / last_fired 用. 正数 "in 5m", 负数 "5m ago".
function fmtRelative(targetSec: number | null): string {
  if (targetSec === null) return "—";
  const diff = targetSec - Date.now() / 1000;
  const abs = Math.abs(diff);
  let s: string;
  if (abs < 60) s = `${Math.floor(abs)}s`;
  else if (abs < 3600) s = `${Math.floor(abs / 60)}m`;
  else if (abs < 86400) s = `${Math.floor(abs / 3600)}h`;
  else s = `${Math.floor(abs / 86400)}d`;
  return diff > 0 ? `in ${s}` : `${s} ago`;
}

function ScheduleRow({ sched }: { sched: AppScheduleTrigger }) {
  return (
    <li
      title={`cron: ${sched.schedule} → ${sched.invocation_id}`}
      className="flex items-center gap-2 rounded-[4px] px-1.5 py-1 hover:bg-[color:var(--color-bg-raised)]"
    >
      <CalendarClock
        size={11}
        className={cn(
          "shrink-0",
          sched.in_flight ? "text-[#6b5400]" : "text-[color:var(--color-thread-file)]",
        )}
      />
      <span className="min-w-0 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)]">
        {sched.name}
      </span>
      <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
        {sched.schedule}
      </span>
      <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
        → {sched.invocation_id}
      </span>
      <span className="flex-1" />
      {sched.last_fired_at !== null && (
        <span
          title={`last fired ${new Date(sched.last_fired_at * 1000).toLocaleString()}`}
          className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]"
        >
          last {fmtRelative(sched.last_fired_at)}
        </span>
      )}
      <span
        title={
          sched.next_fire_at !== null
            ? `next fire ${new Date(sched.next_fire_at * 1000).toLocaleString()}`
            : "cron 计算失败 (异常)"
        }
        className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-paper-dim)]"
      >
        next {fmtRelative(sched.next_fire_at)}
      </span>
      {sched.in_flight && (
        <span
          title="invocation 正在跑"
          className="tabular shrink-0 font-mono text-[10px] text-[#6b5400]"
        >
          ●
        </span>
      )}
    </li>
  );
}

function ServiceRow({
  appName,
  svc,
  onStopped,
}: {
  appName: string;
  svc: AppRunningService;
  onStopped: () => void;
}) {
  const isUp = svc.status === "running";
  const [stopping, setStopping] = useState(false);

  async function handleStop() {
    if (stopping) return;
    setStopping(true);
    try {
      const r = await api.stopAppService(appName, svc.name);
      if (r.stopped) {
        toast.success(`stopped ${svc.name} (pid ${svc.pid})`);
      } else {
        // 后端在 registry 没找到 — 多半被别的 invocation 链清掉了, UI 这边补刷新就行
        toast.info(`${svc.name} 已经不在 registry — 刷新状态`);
      }
      onStopped();
    } catch (e) {
      toast.error(`Stop failed: ${String(e)}`);
    } finally {
      setStopping(false);
    }
  }

  return (
    <li
      title={`log: ${svc.log_path}`}
      className="flex items-center gap-2 rounded-[4px] px-1.5 py-1 hover:bg-[color:var(--color-bg-raised)]"
    >
      <Server
        size={11}
        className={cn(
          "shrink-0",
          isUp ? "text-[#2d5a3d]" : "text-[color:var(--color-ink-dim)]",
        )}
      />
      <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)]">
        {svc.name}
      </span>
      <span
        className={cn(
          "tabular shrink-0 font-mono text-[9.5px] uppercase tracking-wider",
          isUp ? "text-[#2d5a3d]" : "text-[#7a2d2d]",
        )}
      >
        {svc.status}
      </span>
      {svc.port !== null && (
        <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          :{svc.port}
        </span>
      )}
      {svc.pid !== null && (
        <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          pid {svc.pid}
        </span>
      )}
      {isUp && svc.started_at !== null && (
        <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          {fmtUptime(svc.started_at)}
        </span>
      )}
      {svc.last_exit_status !== null && svc.last_exit_status !== 0 && (
        <span
          title={`last exit ${svc.last_exit_status} (launchd)`}
          className="tabular shrink-0 font-mono text-[10px] text-[#b06060]"
        >
          ✗{svc.last_exit_status}
        </span>
      )}
      <button
        type="button"
        onClick={handleStop}
        disabled={stopping || !isUp}
        title={isUp ? "Stop service (释放进程, 下次 invoke 自动重起)" : "已停止"}
        className="shrink-0 rounded-[3px] p-0.5 text-[#b06060] transition-colors hover:bg-[#f0c8c8] hover:text-[#7a2d2d] disabled:cursor-not-allowed disabled:opacity-30"
      >
        {stopping ? (
          <Loader2 size={11} className="animate-spin" />
        ) : (
          <Square size={10} className="fill-current" />
        )}
      </button>
    </li>
  );
}

// Ephemeral service 行 — 跟 ServiceRow 视觉一致但有差异化色:
//   - icon 用 Server (跟 declared 同), 但 dim 色调暗示"非持久"
//   - status 字段固定 alive/dead (按 svc.alive)
//   - 没 Stop 按钮 (前端没 API stop ephemeral, 让 agent 通过 weave_service_stop 管理)
//   - 鼠标悬停 title 显示 log_path
function EphemeralServiceRow({ svc }: { svc: AppEphemeralService }) {
  const isUp = svc.alive;
  return (
    <li
      title={`ephemeral ${svc.service_name} — log: ${svc.log_path}`}
      className="flex items-center gap-2 rounded-[4px] px-1.5 py-1 hover:bg-[color:var(--color-bg-raised)]"
    >
      <Server
        size={11}
        className={cn(
          "shrink-0",
          isUp ? "text-[#7a6a3d]" : "text-[color:var(--color-ink-dim)]",
        )}
      />
      <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)]">
        {svc.service_name}
      </span>
      <span
        className={cn(
          "tabular shrink-0 font-mono text-[9.5px] uppercase tracking-wider",
          isUp ? "text-[#7a6a3d]" : "text-[#7a2d2d]",
        )}
      >
        {isUp ? "ephemeral" : "dead"}
      </span>
      <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
        :{svc.port}
      </span>
      <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
        pid {svc.pid}
      </span>
      {isUp && (
        <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          {fmtUptime(svc.started_at)}
        </span>
      )}
      {!isUp && svc.exit_code !== null && svc.exit_code !== 0 && (
        <span
          title={`exit ${svc.exit_code}`}
          className="tabular shrink-0 font-mono text-[10px] text-[#b06060]"
        >
          ✗{svc.exit_code}
        </span>
      )}
    </li>
  );
}

// E3: WatchRow — 默认收起, 展开时 lazy fetch /watches/{name}/files. SWR key 含
// watchName, 同 modal 多个 watch 互不踩. 列表内点击文件用系统默认 app 打开.
// 头部按钮加 trigger 行 (events + invocation_id) 或 "browse only" 灰字.
function WatchRow({
  appName,
  watchName,
  sessionId,
  trigger,
}: {
  appName: string;
  watchName: string;
  sessionId: string;
  trigger?: AppWatchTrigger;  // 有 invocation_id 才会传, 否则纯 UI 浏览
}) {
  const [open, setOpen] = useState(false);
  const { data, error, isLoading, mutate, isValidating } =
    useSWR<AppWatchFilesResponse>(
      open ? `app-watch:${appName}:${watchName}` : null,
      () => api.listWatchFiles(appName, watchName),
      { revalidateOnFocus: false },
    );

  async function openWatchFile(entry: AppWatchEntry) {
    if (!sessionId) {
      toast.error("Open file requires an active session");
      return;
    }
    try {
      await api.openFile({ sessionId, path: entry.absolute_path });
    } catch (e) {
      toast.error(`Open failed: ${String(e)}`);
    }
  }

  return (
    <li className="rounded-[4px] border border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-soft)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2 py-1 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]"
      >
        {open ? (
          <ChevronDown size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        ) : (
          <ChevronRight size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        )}
        <Eye size={11} className="shrink-0 text-[color:var(--color-thread-file)]" />
        <span className="min-w-0 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)]">
          {watchName}
        </span>
        {/* trigger 状态 — 有 invocation_id 显示 → invocation, 否则 "browse only".
            launchd 不暴露 events / debounce_ms (这俩是织造期声明), 不显示这俩字段. */}
        {trigger ? (
          <span
            title={`fires ${trigger.invocation_id} on file change`}
            className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]"
          >
            → {trigger.invocation_id}
            {trigger.in_flight && <span className="ml-1 text-[#6b5400]">●</span>}
          </span>
        ) : (
          <span
            title="watch.invocation_id 没设, 仅 UI 浏览 (不触发 invocation)"
            className="tabular shrink-0 font-mono text-[10px] italic text-[color:var(--color-ink-dim)]"
          >
            browse only
          </span>
        )}
        <span className="flex-1" />
        {data && (
          <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
            {data.entries.length}
            {data.truncated ? "+" : ""}
          </span>
        )}
      </button>
      {open && (
        <div className="border-t border-[color:var(--color-line-soft)] px-2 py-1.5">
          <div className="mb-1 flex items-center gap-2">
            <span className="tabular flex-1 truncate font-mono text-[10px] text-[color:var(--color-ink-dim)]">
              {data?.path ?? "—"}
            </span>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                mutate();
              }}
              disabled={isValidating}
              title="Refresh files"
              className="rounded-[3px] p-0.5 text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)] disabled:opacity-40"
            >
              <RefreshCw size={10} className={isValidating ? "animate-spin" : ""} />
            </button>
          </div>
          {isLoading && (
            <div className="flex items-center gap-1.5 px-1 py-1 text-[color:var(--color-ink)]">
              <Loader2 size={11} className="animate-spin" />
              <span className="text-[11px] italic">Loading…</span>
            </div>
          )}
          {error && (
            <div className="rounded-[3px] bg-[#f8e8e8] px-1.5 py-1 font-mono text-[10.5px] text-[#7a2d2d]">
              {String(error)}
            </div>
          )}
          {data && data.note && (
            <Placeholder>{data.note}</Placeholder>
          )}
          {data && !data.note && data.entries.length === 0 && (
            <Placeholder>Empty.</Placeholder>
          )}
          {data && data.entries.length > 0 && (
            <ul className="space-y-0.5">
              {data.entries.map((e) => (
                <WatchEntryRow key={e.rel_path} entry={e} onOpen={() => openWatchFile(e)} />
              ))}
            </ul>
          )}
          {data?.truncated && (
            <p className="mt-1 px-1 text-[10px] italic text-[color:var(--color-ink-dim)]">
              truncated at 500 entries — 想看全去 weaver/apps/{appName}/{data.path}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function WatchEntryRow({
  entry,
  onOpen,
}: {
  entry: AppWatchEntry;
  onOpen: () => void;
}) {
  const ext = entry.is_dir ? "" : entry.rel_path.split(".").pop() || "";
  const Icon = entry.is_dir ? AppWindow : iconForExt(ext);
  return (
    <li>
      <button
        type="button"
        onClick={() => !entry.is_dir && onOpen()}
        disabled={entry.is_dir}
        className="group flex w-full items-center gap-2 rounded-[3px] px-1 py-0.5 text-left transition-colors hover:bg-[color:var(--color-bg-raised)] disabled:cursor-default disabled:hover:bg-transparent"
      >
        <Icon size={11} className="shrink-0 text-[color:var(--color-thread-file)]" />
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[color:var(--color-paper-dim)] group-hover:text-[color:var(--color-paper)]">
          {entry.rel_path}
        </span>
        {!entry.is_dir && (
          <span className="tabular shrink-0 font-mono text-[9.5px] text-[color:var(--color-ink-dim)]">
            {fmtBytes(entry.size)}
          </span>
        )}
      </button>
    </li>
  );
}
