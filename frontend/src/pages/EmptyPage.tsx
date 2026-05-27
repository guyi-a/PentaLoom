// 进入 PentaLoom 的第一屏 — 参考 Claude 桌面端版式:
//   - 中央紧凑输入卡片 (textarea + footer 按钮行)
//   - 卡片内左下 [+] 附件按钮 (暂未实现, disabled)
//   - 卡片内右下 [↑] 发送 (accent)
//   - 卡片下方 "Work in a project" 触发 FolderPicker, 已选目录在它后面用 chips 显示
//
// 工作区在选目录的瞬间 + 第一句话同时确定, 不允许中途换 cwd.

import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { ArrowUp, Briefcase, ChevronDown, Folder, Paperclip, X } from "lucide-react";

import { LoomMark } from "@/components/brand/LoomMark";
import { ChatStream } from "@/components/chat/ChatStream";
import { FolderPicker } from "@/components/permission/FolderPicker";
import { chatStream } from "@/lib/api";
import { appendFrame } from "@/lib/frames";
import type { Frame } from "@/lib/types";
import { cn, shortenPath } from "@/lib/utils";

const MAX_MOUNTS = 10;

export function EmptyPage() {
  const navigate = useNavigate();
  const { mutate } = useSWRConfig();

  const [prompt, setPrompt] = useState("");
  const [sentPrompt, setSentPrompt] = useState<string | null>(null);
  const [mounts, setMounts] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [liveSid, setLiveSid] = useState<string | null>(null);
  const [liveFrames, setLiveFrames] = useState<Frame[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function addMount(path: string) {
    if (mounts.includes(path)) return;
    if (mounts.length >= MAX_MOUNTS) {
      toast.error(`At most ${MAX_MOUNTS} directories`);
      return;
    }
    setMounts([...mounts, path]);
    setPickerOpen(false);
  }

  function removeMount(p: string) {
    setMounts(mounts.filter((m) => m !== p));
  }

  async function onSubmit(e?: FormEvent) {
    e?.preventDefault();
    if (sending || !prompt.trim()) return;
    const text = prompt.trim();
    setSending(true);
    setSentPrompt(text);
    setLiveFrames([]);
    setPrompt("");
    try {
      const handle = await chatStream({
        prompt: text,
        mountedDirs: mounts,
      });
      setLiveSid(handle.sessionId);
      mutate("sessions");
      for await (const f of handle.frames) {
        setLiveFrames((prev) => appendFrame(prev, f));
        if (f.type === "stream_end") break;
      }
      navigate(`/s/${handle.sessionId}`);
      mutate("sessions");
    } catch (err) {
      toast.error(`Send failed: ${String(err)}`);
      setSending(false);
      setSentPrompt(null);
      setPrompt(text);
    }
  }

  // 一进入发送态 → 立刻切到 ChatStream 视图 (像 Claude 桌面端一样, 不等后端 sid).
  // sid 没回来前传空串, 反正这阶段不会触发 workspace 授权弹窗 (那是 SDK tool_use 才会推);
  // 拿到 sid 后下面的 stream 循环会 setLiveSid, 顺带刷视图.
  if (sending || liveSid) {
    return (
      <ChatStream
        sessionId={liveSid ?? ""}
        streamedFrames={liveFrames}
        historyMessages={[]}
        localUserPrompt={sentPrompt}
        onUserSend={() => {
          if (liveSid) navigate(`/s/${liveSid}`);
        }}
        inputDisabled
      />
    );
  }

  const canSend = !sending && prompt.trim().length > 0;

  return (
    <>
      <div className="relative h-full overflow-y-auto">
        <div className="mx-auto flex min-h-full max-w-[720px] flex-col justify-center px-6 py-16">
          {/* 标题区 */}
          <div className="mb-8 flex items-center gap-3">
            <LoomMark size={28} active={false} />
            <h1 className="text-[26px] font-semibold leading-tight tracking-tight text-[color:var(--color-paper)]">
              Let's start a new thread
            </h1>
          </div>

          {/* 输入卡片 */}
          <form
            onSubmit={onSubmit}
            onClick={(e) => {
              if ((e.target as HTMLElement).closest("button, textarea")) return;
              textareaRef.current?.focus();
            }}
            className="cursor-text rounded-[12px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] shadow-[0_1px_2px_rgba(20,30,50,0.03)] transition-shadow focus-within:border-[color:var(--color-accent)] focus-within:shadow-[0_0_0_3px_rgba(61,90,128,0.12)]"
          >
            <textarea
              ref={textareaRef}
              value={prompt}
              onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
                setPrompt(e.target.value)
              }
              onKeyDown={(e) => {
                // Enter 发送; Shift+Enter 换行 (浏览器默认); 中文输入法 composing 期间不拦
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  onSubmit();
                }
              }}
              rows={2}
              placeholder="How can PentaLoom help you today?"
              className="block w-full resize-none rounded-t-[12px] bg-transparent px-4 pt-4 pb-2 text-[14px] leading-relaxed text-[color:var(--color-paper)] placeholder:text-[color:var(--color-ink-dim)] focus:outline-none"
            />

            <div className="flex items-center justify-between gap-3 px-2.5 pb-2.5 pt-1">
              {/* 附件 — 后端 /chat 还没接 multipart, 先 stub */}
              <button
                type="button"
                onClick={() =>
                  toast.info("Attachments coming soon — backend pipeline not wired yet")
                }
                title="Attach files (coming soon)"
                className="flex h-9 w-9 items-center justify-center rounded-[8px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
              >
                <Paperclip size={17} />
              </button>

              <button
                type="submit"
                disabled={!canSend}
                title="Send (⌘/Ctrl + Enter)"
                className={cn(
                  "flex h-9 w-9 items-center justify-center rounded-[8px] transition-colors",
                  canSend
                    ? "bg-[color:var(--color-accent)] text-white hover:opacity-90"
                    : "cursor-not-allowed bg-[color:var(--color-bg-raised)] text-[color:var(--color-ink)]",
                )}
              >
                <ArrowUp size={17} />
              </button>
            </div>
          </form>

          {/* 卡片下方: 项目选择器 + 已挂载目录 chips */}
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              disabled={mounts.length >= MAX_MOUNTS}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-[8px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] py-1.5 pl-2.5 pr-2 text-[12px] transition-colors",
                mounts.length >= MAX_MOUNTS
                  ? "cursor-not-allowed text-[color:var(--color-ink-dim)]"
                  : "text-[color:var(--color-paper-dim)] hover:border-[color:var(--color-line-strong)] hover:text-[color:var(--color-paper)]",
              )}
              title="Choose folders PentaLoom can read & write"
            >
              <Briefcase size={13} className="text-[color:var(--color-paper-dim)]" />
              <span>Work in a project</span>
              <ChevronDown size={12} className="text-[color:var(--color-ink)]" />
            </button>

            {mounts.map((m) => (
              <span
                key={m}
                className="inline-flex items-center gap-1.5 rounded-[8px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] py-1.5 pl-2 pr-1 font-mono text-[11px] text-[color:var(--color-paper-dim)]"
              >
                <Folder
                  size={11}
                  className="text-[color:var(--color-thread-file)]"
                />
                <span className="max-w-[240px] truncate" title={m}>
                  {shortenPath(m, 36)}
                </span>
                <button
                  type="button"
                  onClick={() => removeMount(m)}
                  className="rounded-[3px] p-0.5 text-[color:var(--color-ink)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-error)]"
                  title="Remove"
                >
                  <X size={10} />
                </button>
              </span>
            ))}

            {mounts.length > 0 && (
              <span className="ml-1 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
                {mounts.length}/{MAX_MOUNTS}
              </span>
            )}
          </div>
        </div>
      </div>

      {pickerOpen && (
        <FolderPicker
          alreadyAdded={mounts}
          onCancel={() => setPickerOpen(false)}
          onSelect={addMount}
        />
      )}
    </>
  );
}
