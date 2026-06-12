// 单个会话页 /s/:sid:
// - 顶部: 会话标题 (可改) + PanelRight 切换
// - 中部: 左 (ChatStream 历史 + 流帧 + 输入) + 右 (RightPanel: Todo/Workspace/Context)
//
// 数据加载 + resume:
// - SWR 拿 SessionMeta + 历史 messages
// - mount / sid 变更时, 自动调 GET /chat/{sid}/resume 重连任何"正在跑的 turn":
//   - 后端 buffer 不存在 / status=COMPLETE → 204 → 不做任何事 (历史已经全在 SWR 里)
//   - 否则 (status=STREAMING) → 全量回放 buffer.chunks + 续订阅, 注入 pending
//     审批 chunk, 把 frames 一帧帧 append 到 liveFrames. 用户切走再回来 / 刷新
//     / 多 tab 同 session 都能毫发无损看到正在跑的 turn + 现在挂着的审批.
// - 用户发送: chatStream POST /chat → 后端起后台 task 跑 query, 返回 stream_all().
//   用户中途切走只 abort 本地 fetch, 后端 task 不被打断; 回来 resume 续接.
//
// 三栏 layout 响应式 (持久化在 localStorage):
// - >= 1280: 默认展开 340px
// - 768-1280: 默认收起, 展开走 320px
// - < 768: 展开成全屏 drawer 覆盖
// - 用户手动 toggle 后写 localStorage, 优先级高于默认

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useParams } from "react-router";
import { toast } from "sonner";
import useSWR, { useSWRConfig } from "swr";
import { Check, PanelRightClose, PanelRightOpen, Pencil } from "lucide-react";

import { ChatStream } from "@/components/chat/ChatStream";
import { FilePreviewPanel } from "@/components/right-panel/file-preview/FilePreviewPanel";
import { RightPanel } from "@/components/right-panel/RightPanel";
import { api, chatStream, resumeChat } from "@/lib/api";
import { appendFrame } from "@/lib/frames";
import { MAIN_CONTENT_MIN_WIDTH } from "@/lib/layout-constraints";
import {
  PREVIEW_WIDTH_MAX,
  PREVIEW_WIDTH_MIN,
  usePreviewStore,
} from "@/lib/preview-store";
import type { Frame } from "@/lib/types";
import { cn } from "@/lib/utils";

function isAbortError(err: unknown): boolean {
  return (
    err instanceof DOMException && err.name === "AbortError"
  ) || (err as { name?: string })?.name === "AbortError";
}

const PANEL_LS_KEY = "pentaloom:right-panel:open";
const PANEL_WIDTH_LS_KEY = "pentaloom:right-panel:width";
const PANEL_MIN = 280;
const PANEL_DEFAULT = 340;
const PANEL_MAX = 520;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// 按当前 viewport + localStorage 决定 panel 初始展开状态.
// 默认: >=1280 开; <1280 收. localStorage 有值就覆盖默认.
function initialPanelOpen(): boolean {
  if (typeof window === "undefined") return true;
  const saved = window.localStorage.getItem(PANEL_LS_KEY);
  if (saved === "true") return true;
  if (saved === "false") return false;
  return window.innerWidth >= 1280;
}

function initialPanelWidth(): number {
  if (typeof window === "undefined") return PANEL_DEFAULT;
  const saved = Number(window.localStorage.getItem(PANEL_WIDTH_LS_KEY));
  return Number.isFinite(saved) ? clamp(saved, PANEL_MIN, PANEL_MAX) : PANEL_DEFAULT;
}

export function ChatPage() {
  const { sid } = useParams();
  const { mutate: globalMutate } = useSWRConfig();

  const {
    data: meta,
    error: metaError,
    mutate: mutateMeta,
  } = useSWR(sid ? ["session", sid] : null, () => api.getSession(sid!));

  const {
    data: history,
    error: historyError,
    mutate: mutateHistory,
  } = useSWR(sid ? ["messages", sid] : null, () => api.getMessages(sid!));

  // ── 现场流 ────────────────────────────────────────────────
  const [liveFrames, setLiveFrames] = useState<Frame[]>([]);
  const [localUserPrompt, setLocalUserPrompt] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // 当前活跃的 SSE 流的 abort 函数 (chatStream 或 resumeChat 都用同一槽位 —
  // 任一时刻只会有一个: send() 跑时不会 resume, resume 跑完才 setSending(false))
  const abortRef = useRef<(() => void) | null>(null);

  // ── 右栏开关 ─────────────────────────────────────────────
  const [panelOpen, setPanelOpenRaw] = useState<boolean>(initialPanelOpen);
  const [panelWidth, setPanelWidthRaw] = useState<number>(initialPanelWidth);
  // ChatPage 持有真值, 但要写 localStorage. setPanelOpen 包装一下.
  function setPanelOpen(next: boolean) {
    setPanelOpenRaw(next);
    try {
      window.localStorage.setItem(PANEL_LS_KEY, next ? "true" : "false");
    } catch {
      /* localStorage 不可用就算了 */
    }
  }

  function setPanelWidth(next: number) {
    const width = clamp(Math.round(next), PANEL_MIN, PANEL_MAX);
    setPanelWidthRaw(width);
    try {
      window.localStorage.setItem(PANEL_WIDTH_LS_KEY, String(width));
    } catch {
      /* localStorage 不可用就算了 */
    }
  }

  function beginPanelResize(e: ReactPointerEvent<HTMLDivElement>) {
    if (!desktopPanelVisible) return;
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = panelWidth;
    const previousCursor = document.body.style.cursor;
    const previousSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    function onMove(ev: PointerEvent) {
      const maxByMain = e.currentTarget.parentElement?.parentElement
        ? e.currentTarget.parentElement.parentElement.clientWidth - MAIN_CONTENT_MIN_WIDTH
        : PANEL_MAX;
      const maxWidth = Math.max(PANEL_MIN, Math.min(PANEL_MAX, maxByMain));
      setPanelWidth(clamp(startWidth - (ev.clientX - startX), PANEL_MIN, maxWidth));
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

  // viewport <768 时, 右栏走 fixed drawer 模式, 主区不让出宽度. 用 matchMedia.
  const [isMobile, setIsMobile] = useState<boolean>(() =>
    typeof window !== "undefined" ? window.innerWidth < 768 : false,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const cb = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", cb);
    return () => mq.removeEventListener("change", cb);
  }, []);

  // sid 切换 / 首次 mount: 清场 + 自动 resume 进行中的 turn
  useEffect(() => {
    let cancelled = false;
    setLiveFrames([]);
    setLocalUserPrompt(null);
    setSending(false);

    if (!sid) return;

    (async () => {
      // resume 永远走 stream_all (delta 折叠 + 续订阅). 后端 + 前端 reducer
      // 都按 (msg_uuid, index) 幂等, ChatStream 按 history uuid 跨源去重 — 即使
      // 跟 SWR /messages 拉到的历史 race 也不会双份, thinking 也不会消失.
      // 服务端在 buffer 不存在或 status=COMPLETE 时回 204.
      let handle: { frames: AsyncIterable<Frame>; abort: () => void } | null = null;
      try {
        handle = await resumeChat({ sessionId: sid });
      } catch (err) {
        if (!cancelled) console.warn("resume request failed", err);
        return;
      }
      if (!handle) return; // 没有在跑的 turn
      if (cancelled) {
        handle.abort();
        return;
      }
      setSending(true);
      abortRef.current = handle.abort;
      try {
        for await (const f of handle.frames) {
          if (cancelled) break;
          if (f.type === "user_prompt") {
            // 后端在 resume 首帧塞回这轮 turn 的 user prompt — 用来补 "刷新后
            // localUserPrompt 是 null, JSONL 又没 catch up" 的空窗.
            setLocalUserPrompt(f.text);
            continue;
          }
          setLiveFrames((prev) => appendFrame(prev, f));
          if (f.type === "stream_end") break;
        }
        if (!cancelled) {
          // turn 跑完 — 让 history + meta 重拉, 把 liveFrames 还给历史
          await mutateHistory();
          await mutateMeta();
          globalMutate("sessions");
          setLiveFrames([]);
          setLocalUserPrompt(null);
        }
      } catch (err) {
        // 用户切走 / 刷新触发的 abort 是预期; 别 toast
        if (!cancelled && !isAbortError(err)) {
          console.warn("resume stream error", err);
        }
      } finally {
        if (!cancelled) {
          setSending(false);
          abortRef.current = null;
        }
      }
    })();

    return () => {
      cancelled = true;
      abortRef.current?.();
      abortRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid]);

  async function send(prompt: string) {
    if (!sid || sending) return;
    setSending(true);
    setLocalUserPrompt(prompt);
    setLiveFrames([]);
    try {
      const handle = await chatStream({ prompt, sessionId: sid });
      abortRef.current = handle.abort;
      for await (const f of handle.frames) {
        setLiveFrames((prev) => appendFrame(prev, f));
        if (f.type === "stream_end") break;
      }
      // 一轮 turn 结束 → 把现场流合进历史 (后端 jsonl 已落, 但读回有延时, 先用本地拼)
      await mutateHistory();
      // mounted_dirs 可能在本轮被 workspace 工具更新过 → 刷新 meta + sidebar
      await mutateMeta();
      globalMutate("sessions");
      // weaver 产物 (skill / app) 可能在本轮被 weave / edit / delete 改过 → 刷 sidebar.
      // 不扫帧判断, 直接 mutate — GET /weaver/products 廉价, 漏判反而麻烦.
      globalMutate("weaver/products");
      setLiveFrames([]);
      setLocalUserPrompt(null);
    } catch (err) {
      // 用户切走 sid 时 cleanup 会 abort 本地 fetch — 这是预期, 别 toast.
      // 后端 background task 仍在跑, 回来时 resume 能接上.
      if (!isAbortError(err)) {
        toast.error(`Send failed: ${String(err)}`);
      }
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }

  // 中断当前 turn — 后端两步杀 (SDK interrupt + stream task cancel).
  // 不主动 setSending(false), 让 stream_end 帧自然走 for-await break → finally setSending(false),
  // 跟正常 turn 结束同款路径. 失败 toast 一下, 用户能 retry / refresh.
  async function stop() {
    if (!sid || !sending) return;
    try {
      await api.stopChat(sid);
    } catch (err) {
      toast.error(`Stop failed: ${String(err)}`);
    }
  }

  // 给 RightPanel 用 — Workspace 改完调这里刷 meta + sidebar (mounted_dirs 是 SessionMeta 的字段)
  const onMountsChanged = useMemo(
    () => () => {
      mutateMeta();
      globalMutate("sessions");
    },
    [mutateMeta, globalMutate],
  );

  // File preview — Context chip 点击触发, 替换 RightPanel 槽位 (preview 模式).
  // hook 必须放 early return 之前 (React rules of hooks).
  // sid 切换时清掉 (跨 session preview 没意义).
  const previewFile = usePreviewStore((s) => s.previewFile);
  const closePreview = usePreviewStore((s) => s.closePreview);
  const previewWidth = usePreviewStore((s) => s.previewWidth);
  const setPreviewWidth = usePreviewStore((s) => s.setPreviewWidth);
  useEffect(() => {
    closePreview();
  }, [sid, closePreview]);

  // preview 列左边缘 resize handle. 跟 RightPanel 同款 pointer 模式 — pointermove
  // 直接计算新宽度 + clamp + 写 localStorage. 跨 chat 主区时强制 min-width 防主区压没.
  function beginPreviewResize(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = previewWidth;
    const previousCursor = document.body.style.cursor;
    const previousSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const containerEl = e.currentTarget.parentElement?.parentElement ?? null;
    function onMove(ev: PointerEvent) {
      const maxByMain = containerEl
        ? containerEl.clientWidth - MAIN_CONTENT_MIN_WIDTH
        : PREVIEW_WIDTH_MAX;
      const maxWidth = Math.max(
        PREVIEW_WIDTH_MIN,
        Math.min(PREVIEW_WIDTH_MAX, maxByMain),
      );
      // 鼠标向左拖 → 列变宽 (列在右边). delta = startX - ev.clientX
      const next = Math.max(
        PREVIEW_WIDTH_MIN,
        Math.min(maxWidth, startWidth + (startX - ev.clientX)),
      );
      setPreviewWidth(next);
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

  if (metaError) {
    return (
      <div className="flex h-full items-center justify-center px-6">
        <div className="rounded-[5px] border border-[color:var(--color-error)] bg-[color:var(--color-bg-card)] px-4 py-3 text-[12px] text-[color:var(--color-error)]">
          Failed to load session: {String(metaError)}
        </div>
      </div>
    );
  }

  if (!meta) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-[12px] text-[color:var(--color-ink)]">
        Loading…
      </div>
    );
  }

  // 右栏在 desktop (>=768) 时占内联宽度; 在 mobile 时悬浮 drawer 覆盖.
  // preview 模式: 占用 RightPanel 同一个槽位, RightPanel 让位 (跟 krow 同款 mode 切换).
  // 槽位宽度仍用 panelWidth — 用户拖拽过的宽度对 preview 也合适.
  const showPreviewColumn = !isMobile && previewFile !== null;
  const desktopPanelVisible = !isMobile && panelOpen && !showPreviewColumn;
  const mobilePanelVisible = isMobile && panelOpen;

  return (
    <div className="flex h-full flex-col">
      <ChatHeader
        sessionId={meta.session_id}
        title={meta.title}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen(!panelOpen)}
        onTitleChange={async (next) => {
          try {
            await api.patchSession(meta.session_id, { title: next });
            await mutateMeta();
            globalMutate("sessions");
          } catch (err) {
            toast.error(`Rename failed: ${String(err)}`);
          }
        }}
      />
      <div className="min-h-0 flex-1">
        {historyError ? (
          <div className="flex h-full items-center justify-center px-6">
            <div className="rounded-[5px] border border-[color:var(--color-error)] bg-[color:var(--color-bg-card)] px-4 py-3 text-[12px] text-[color:var(--color-error)]">
              Failed to load history: {String(historyError)}
            </div>
          </div>
        ) : (
          <div className="flex h-full min-h-0">
            <div className="min-w-0 flex-1">
              <ChatStream
                sessionId={meta.session_id}
                historyMessages={history ?? []}
                streamedFrames={liveFrames}
                localUserPrompt={localUserPrompt}
                onUserSend={send}
                onStop={stop}
                inputDisabled={sending}
              />
            </div>
            {desktopPanelVisible && (
              <div className="relative shrink-0" style={{ width: panelWidth }}>
                <div
                  role="separator"
                  aria-orientation="vertical"
                  title="Drag to resize panel"
                  onPointerDown={beginPanelResize}
                  className="absolute left-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[color:var(--color-accent)]/20"
                />
                <RightPanel
                  sessionId={meta.session_id}
                  meta={meta}
                  history={history ?? []}
                  liveFrames={liveFrames}
                  onMountsChanged={onMountsChanged}
                />
              </div>
            )}
            {showPreviewColumn && (
              <div
                className="relative shrink-0"
                style={{ width: previewWidth }}
              >
                {/* 左边缘 resize handle. 跟 RightPanel 的 separator 同款视觉 + 拖动逻辑. */}
                <div
                  role="separator"
                  aria-orientation="vertical"
                  title="Drag to resize preview"
                  onPointerDown={beginPreviewResize}
                  className="absolute left-[-3px] top-0 z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[color:var(--color-accent)]/20"
                />
                <FilePreviewPanel sessionId={meta.session_id} />
              </div>
            )}
          </div>
        )}
      </div>

      {/* mobile drawer — 全屏覆盖, 半透明黑遮罩点击关闭 */}
      {mobilePanelVisible && (
        <div className="fixed inset-0 z-40">
          <button
            type="button"
            aria-label="Close panel"
            onClick={() => setPanelOpen(false)}
            className="absolute inset-0 bg-black/30 backdrop-blur-sm"
          />
          <div className="absolute right-0 top-0 h-full w-[min(360px,90vw)]">
            <RightPanel
              sessionId={meta.session_id}
              meta={meta}
              history={history ?? []}
              liveFrames={liveFrames}
              onMountsChanged={onMountsChanged}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ──── 顶部头 ─────────────────────────────────────────────────
function ChatHeader({
  sessionId,
  title,
  panelOpen,
  onTogglePanel,
  onTitleChange,
}: {
  sessionId: string;
  title: string | null;
  panelOpen: boolean;
  onTogglePanel: () => void;
  onTitleChange: (next: string | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title ?? "");

  useEffect(() => {
    setDraft(title ?? "");
  }, [title]);

  function commit() {
    const next = draft.trim();
    setEditing(false);
    if (next === (title ?? "")) return;
    onTitleChange(next === "" ? null : next);
  }

  return (
    <div className="app-drag-region relative z-20 border-b border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-6 py-3">
      <div className="flex items-center justify-between gap-4">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {editing ? (
            <div className="app-no-drag flex min-w-0 max-w-[min(680px,70vw)] flex-1 items-center gap-2 rounded-[8px] border border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-card)] px-2.5 py-1.5 shadow-[0_1px_2px_rgba(20,30,50,0.04)] focus-within:border-[color:var(--color-accent)] focus-within:shadow-[0_0_0_3px_rgba(61,90,128,0.10)]">
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={commit}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    (e.target as HTMLInputElement).blur();
                  }
                  if (e.key === "Escape") {
                    setDraft(title ?? "");
                    setEditing(false);
                  }
                }}
                placeholder="Name this thread…"
                className="font-display min-w-0 flex-1 bg-transparent text-[16px] font-medium tracking-[-0.01em] text-[color:var(--color-paper)] placeholder:text-[color:var(--color-ink-dim)] focus:outline-none"
              />
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="app-no-drag font-display group flex min-w-0 items-center gap-2 truncate text-left text-[16px] font-medium tracking-[-0.01em] text-[color:var(--color-paper)] hover:text-[color:var(--color-accent)]"
              title="Click to rename"
            >
              <span className="truncate">
                {title ?? (
                  <span className="font-mono text-[12px] text-[color:var(--color-ink)]">
                    {sessionId.slice(0, 8)}
                  </span>
                )}
              </span>
              <Pencil
                size={12}
                className="shrink-0 opacity-0 transition-opacity group-hover:opacity-60"
              />
            </button>
          )}
          {editing && (
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={commit}
              className="app-no-drag shrink-0 text-[color:var(--color-accent)]"
            >
              <Check size={14} />
            </button>
          )}
        </div>

        <button
          type="button"
          onClick={onTogglePanel}
          title={panelOpen ? "Hide panel" : "Show panel"}
          className={cn(
            "app-no-drag shrink-0 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-2 py-1.5 transition-colors hover:border-[color:var(--color-line-strong)]",
            panelOpen
              ? "text-[color:var(--color-paper-dim)]"
              : "text-[color:var(--color-ink)]",
          )}
        >
          {panelOpen ? <PanelRightClose size={12} /> : <PanelRightOpen size={12} />}
        </button>
      </div>
    </div>
  );
}
