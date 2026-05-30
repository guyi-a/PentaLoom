// 进入 PentaLoom 的第一屏:
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
import { ArrowUp, Folder, FolderPlus, Paperclip, X } from "lucide-react";

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
      navigate(`/s/${handle.sessionId}`);
      mutate("sessions");
      for await (const f of handle.frames) {
        setLiveFrames((prev) => appendFrame(prev, f));
        if (f.type === "stream_end") break;
      }
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
      <div className="weave-texture relative h-full overflow-y-auto">
        <div className="mx-auto flex min-h-full max-w-[720px] flex-col justify-center px-6 py-16">
          {/* 标题区 — LoomMark 大版 + Fraunces italic 标语 + body 副标语 */}
          <div className="mb-10">
            <LoomMark size={48} active className="mb-6" />
            <h1 className="font-display text-[40px] italic font-normal leading-[1.05] tracking-[-0.015em] text-[color:var(--color-paper)]">
              Five threads,
              <br />
              one weave.
            </h1>
            <p className="mt-4 max-w-[420px] text-[14px] leading-relaxed text-[color:var(--color-ink)]">
              Tell PentaLoom what to read, browse, run, search, or build —
              one prompt, five capabilities weaving in concert.
            </p>
          </div>

          {/* 输入卡片 — 16px 大圆角, focus 时钢蓝光圈, hover 微 lift */}
          <form
            onSubmit={onSubmit}
            onClick={(e) => {
              if ((e.target as HTMLElement).closest("button, textarea")) return;
              textareaRef.current?.focus();
            }}
            className="cursor-text rounded-[16px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] shadow-[0_1px_2px_rgba(20,30,50,0.03)] transition-all hover:shadow-[0_4px_16px_rgba(20,30,50,0.06)] focus-within:border-[color:var(--color-accent)] focus-within:shadow-[0_0_0_3px_rgba(61,90,128,0.12)]"
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
              placeholder="Begin a thread…"
              className="block w-full resize-none rounded-t-[16px] bg-transparent px-5 pt-5 pb-3 text-[15px] leading-relaxed text-[color:var(--color-paper)] placeholder:font-display placeholder:italic placeholder:text-[color:var(--color-ink-dim)] focus:outline-none"
            />

            <div className="flex items-center justify-between gap-3 border-t border-[color:var(--color-line-soft)] px-3 py-2.5">
              {/* 附件 — 后端 /chat 还没接 multipart, 先 stub */}
              <button
                type="button"
                onClick={() =>
                  toast.info("Attachments coming soon — backend pipeline not wired yet")
                }
                title="Attach files (coming soon)"
                className="flex h-10 w-10 items-center justify-center rounded-[8px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
              >
                <Paperclip size={17} />
              </button>

              <button
                type="submit"
                disabled={!canSend}
                title="Send (⌘/Ctrl + Enter)"
                className={cn(
                  "flex h-10 w-10 items-center justify-center rounded-[8px] transition-colors",
                  canSend
                    ? "bg-[color:var(--color-accent)] text-white hover:opacity-90"
                    : "cursor-not-allowed bg-[color:var(--color-bg-raised)] text-[color:var(--color-ink)]",
                )}
              >
                <ArrowUp size={17} />
              </button>
            </div>
          </form>

          {/* 卡片下方: 挂载入口 (dashed border chip, 比 ghost 强 affordance) + 已挂载目录 chips */}
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              disabled={mounts.length >= MAX_MOUNTS}
              className={cn(
                "inline-flex items-center gap-2 rounded-[6px] border border-dashed px-2.5 py-1.5 text-[12px] transition-colors",
                mounts.length >= MAX_MOUNTS
                  ? "cursor-not-allowed border-[color:var(--color-line)] text-[color:var(--color-ink-dim)]"
                  : "border-[color:var(--color-line-strong)] text-[color:var(--color-paper-dim)] hover:border-[color:var(--color-accent)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]",
              )}
              title="Mount folders for PentaLoom to read & write"
            >
              <FolderPlus
                size={13}
                className="shrink-0 text-[color:var(--color-ink)]"
              />
              <span>Mount folders</span>
              {mounts.length > 0 && (
                <span className="tabular font-mono text-[10.5px] text-[color:var(--color-ink-dim)]">
                  · {mounts.length}/{MAX_MOUNTS}
                </span>
              )}
            </button>

            {mounts.map((m) => (
              <span
                key={m}
                className="inline-flex items-center gap-1.5 rounded-[6px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] py-1 pl-2 pr-1 font-mono text-[11px] text-[color:var(--color-paper-dim)]"
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
