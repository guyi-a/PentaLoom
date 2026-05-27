// 单个会话页 /s/:sid:
// - 顶部: 会话标题 (可改) + 挂载目录 chips + 删除按钮
// - 中部: ChatStream (历史 + 现场流帧 + 授权弹窗 + 输入框)
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

import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import useSWR, { useSWRConfig } from "swr";
import { Check, Pencil, Trash2 } from "lucide-react";

import { ChatStream } from "@/components/chat/ChatStream";
import { api, chatStream, resumeChat } from "@/lib/api";
import { appendFrame } from "@/lib/frames";
import type { Frame } from "@/lib/types";
import { cn, shortenPath } from "@/lib/utils";

function isAbortError(err: unknown): boolean {
  return (
    err instanceof DOMException && err.name === "AbortError"
  ) || (err as { name?: string })?.name === "AbortError";
}

export function ChatPage() {
  const { sid } = useParams();
  const navigate = useNavigate();
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

  return (
    <div className="flex h-full flex-col">
      <ChatHeader
        sessionId={meta.session_id}
        title={meta.title}
        mountedDirs={meta.mounted_dirs}
        onTitleChange={async (next) => {
          try {
            await api.patchSession(meta.session_id, { title: next });
            await mutateMeta();
            globalMutate("sessions");
          } catch (err) {
            toast.error(`Rename failed: ${String(err)}`);
          }
        }}
        onDelete={async () => {
          if (!confirm(`Delete thread ${meta.session_id.slice(0, 8)}?`)) return;
          try {
            await api.deleteSession(meta.session_id);
            toast.success("Deleted");
            globalMutate("sessions");
            navigate("/");
          } catch (err) {
            toast.error(`Delete failed: ${String(err)}`);
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
          <ChatStream
            sessionId={meta.session_id}
            historyMessages={history ?? []}
            streamedFrames={liveFrames}
            localUserPrompt={localUserPrompt}
            onUserSend={send}
            inputDisabled={sending}
          />
        )}
      </div>
    </div>
  );
}

// ──── 顶部头 ─────────────────────────────────────────────────
function ChatHeader({
  sessionId,
  title,
  mountedDirs,
  onTitleChange,
  onDelete,
}: {
  sessionId: string;
  title: string | null;
  mountedDirs: string[];
  onTitleChange: (next: string | null) => void;
  onDelete: () => void;
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
    <div className="border-b border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-6 py-3">
      <div className="mx-auto flex max-w-[760px] items-center justify-between gap-4">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {editing ? (
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
              className="min-w-0 flex-1 rounded-[5px] border border-[color:var(--color-accent)] bg-[color:var(--color-bg-card)] px-2 py-1 text-[15px] font-medium text-[color:var(--color-paper)] focus:outline-none"
            />
          ) : (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="group flex min-w-0 items-center gap-2 truncate text-left text-[15px] font-medium tracking-tight text-[color:var(--color-paper)] hover:text-[color:var(--color-accent)]"
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
              className="shrink-0 text-[color:var(--color-accent)]"
            >
              <Check size={14} />
            </button>
          )}
        </div>

        <button
          type="button"
          onClick={onDelete}
          className="shrink-0 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-2 py-1.5 text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-error)] hover:text-[color:var(--color-error)]"
          title="Delete thread"
        >
          <Trash2 size={12} className="inline" />
        </button>
      </div>

      {/* mounted dirs 横条 */}
      {mountedDirs.length > 0 && (
        <div className="mx-auto mt-2 flex max-w-[760px] flex-wrap items-center gap-1.5">
          <span className="text-[10px] font-medium uppercase tracking-[0.15em] text-[color:var(--color-ink-dim)]">
            mounts
          </span>
          {mountedDirs.map((d) => (
            <span
              key={d}
              title={d}
              className={cn(
                "max-w-[180px] truncate rounded-[4px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-2 py-0.5 font-mono text-[10px] text-[color:var(--color-paper-dim)]",
              )}
            >
              {shortenPath(d, 32)}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
