// 单个会话页 /s/:sid:
// - 顶部: 会话标题 (可改) + 挂载目录 chips + 删除按钮
// - 中部: ChatStream (历史 + 现场流帧 + 授权弹窗 + 输入框)
//
// 数据加载:
// - SWR 拿 SessionMeta (用于 sidebar 也共享同一份 'sessions' key, 但这里需要单条所以单独 key)
// - SWR 拿 历史 messages
// - 现场流: 用户每次发送 → 开 chatStream, frames 推进 liveFrames

import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import useSWR, { useSWRConfig } from "swr";
import { Check, Pencil, Trash2 } from "lucide-react";

import { ChatStream } from "@/components/chat/ChatStream";
import { api, chatStream } from "@/lib/api";
import { appendFrame } from "@/lib/frames";
import type { Frame } from "@/lib/types";
import { cn, shortenPath } from "@/lib/utils";

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
  const abortRef = useRef<(() => void) | null>(null);

  // sid 切换时清场
  useEffect(() => {
    setLiveFrames([]);
    setLocalUserPrompt(null);
    setSending(false);
    return () => {
      abortRef.current?.();
      abortRef.current = null;
    };
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
      toast.error(`Send failed: ${String(err)}`);
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
