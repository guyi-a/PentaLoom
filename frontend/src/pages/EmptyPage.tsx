// 进入 PentaLoom 的第一屏:
//   - 中央紧凑输入卡片 (textarea + footer 按钮行)
//   - 卡片内左下 [+] 附件按钮 (暂未实现, disabled)
//   - 卡片内右下 [↑] 发送 (accent)
//   - 卡片下方 "Work in a project" 触发 FolderPicker, 已选目录在它后面用 chips 显示
//
// 工作区在选目录的瞬间 + 第一句话同时确定, 不允许中途换 cwd.

import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type FormEvent,
} from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { ArrowUp, Folder, FolderPlus, Paperclip, X } from "lucide-react";

import { LoomMark } from "@/components/brand/LoomMark";
import { ChatStream } from "@/components/chat/ChatStream";
import { FolderPicker } from "@/components/permission/FolderPicker";
import { RightPanel } from "@/components/right-panel/RightPanel";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { chatStream, chatStreamWithAttachments } from "@/lib/api";
import { appendFrame } from "@/lib/frames";
import type { Frame, SessionMeta } from "@/lib/types";
import { cn, shortenPath } from "@/lib/utils";

const MAX_MOUNTS = 10;

// composer state — 跟 PromptInput.DraftAttachment / PastedImage 同结构, 但
// EmptyPage 用自己一份起始卡片 (textarea + 工具行), 不复用 PromptInput.
// inline 在这里避免跨组件折腾.
interface DraftAttachment {
  id: string;
  file: File;
  name: string;
  size: number;
}

interface PastedImage {
  id: string;
  file: File;
  name: string;
  size: number;
  previewUrl: string;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function EmptyPage() {
  const navigate = useNavigate();
  const { mutate } = useSWRConfig();

  const [prompt, setPrompt] = useState("");
  const [drafts, setDrafts] = useState<DraftAttachment[]>([]);
  const [pastedImages, setPastedImages] = useState<PastedImage[]>([]);
  const [sentPrompt, setSentPrompt] = useState<string | null>(null);
  const [sentAttachmentCount, setSentAttachmentCount] = useState<number>(0);
  // 内嵌图缩略图 src 列 (live blob URL) — 发送后 user bubble 渲缩略图直到 ChatPage
  // 接管渲染历史. EmptyPage 是新会话起点, sent 后 navigate 走 → state 整体卸载,
  // 不需要手动 revoke (浏览器在 unload 时回收).
  const [sentInlineImages, setSentInlineImages] = useState<{ src: string }[]>([]);
  const [sentInlineImageCount, setSentInlineImageCount] = useState<number>(0);
  const [mounts, setMounts] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [sending, setSending] = useState(false);
  const [liveSid, setLiveSid] = useState<string | null>(null);
  const [liveFrames, setLiveFrames] = useState<Frame[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 卸载时 revoke 所有 blob URL — 防 long-lived previewUrl 泄漏.
  useEffect(() => {
    return () => {
      pastedImages.forEach((img) => URL.revokeObjectURL(img.previewUrl));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onPickFiles(e: ChangeEvent<HTMLInputElement>) {
    const list = e.target.files;
    if (!list || list.length === 0) return;
    const next: DraftAttachment[] = [];
    for (let i = 0; i < list.length; i++) {
      const f = list.item(i);
      if (!f) continue;
      next.push({ id: crypto.randomUUID(), file: f, name: f.name, size: f.size });
    }
    setDrafts((prev) => [...prev, ...next]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const items = e.clipboardData?.items;
    if (!items) return;
    const collected: PastedImage[] = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind !== "file") continue;
      if (!it.type.startsWith("image/")) continue;
      const f = it.getAsFile();
      if (!f) continue;
      collected.push({
        id: crypto.randomUUID(),
        file: f,
        name: f.name || `pasted-${Date.now()}.${(it.type.split("/")[1] || "png")}`,
        size: f.size,
        previewUrl: URL.createObjectURL(f),
      });
    }
    if (collected.length === 0) return;
    e.preventDefault();
    setPastedImages((prev) => [...prev, ...collected]);
  }

  function removeDraft(id: string) {
    setDrafts((prev) => prev.filter((d) => d.id !== id));
  }

  function removeImage(id: string) {
    setPastedImages((prev) => {
      const target = prev.find((p) => p.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((p) => p.id !== id);
    });
  }

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
    if (sending) return;
    const text = prompt.trim();
    // 接受 "纯文本" / "纯附件" / "纯图片" / 混合; 都没有时 no-op.
    if (!text && drafts.length === 0 && pastedImages.length === 0) return;

    const files = drafts.map((d) => d.file);
    const inlineImages = pastedImages.map((p) => p.file);
    setSending(true);
    setSentPrompt(text);
    setSentAttachmentCount(files.length);
    setSentInlineImageCount(inlineImages.length);
    // 给 user bubble 渲缩略图用 — 父组件单持一份 blob URL (跟 composer 那份解耦);
    // EmptyPage 卸载时浏览器回收 (跟下面 pastedImages.forEach revoke 同源, 但
    // 这里我们持的 URL 跟 PastedImage.previewUrl 是不同对象, 各管各的).
    setSentInlineImages(
      inlineImages.map((f) => ({ src: URL.createObjectURL(f) })),
    );
    setLiveFrames([]);
    setPrompt("");
    setDrafts([]);
    // submit 时不 revoke previewUrl — pastedImages 还在 sentXxx 视图里被需要 (chip 没了
    // 不展示, 但 File 对象在 inlineImages 里还要传给后端). 简单清空 state, blob URL
    // 在 unmount 的 useEffect cleanup 时统一 revoke.
    setPastedImages([]);
    try {
      const hasAnyAttachment = files.length > 0 || inlineImages.length > 0;
      const handle = hasAnyAttachment
        ? await chatStreamWithAttachments({
            prompt: text,
            mountedDirs: mounts,
            files,
            inlineImages,
          })
        : await chatStream({
            prompt: text,
            mountedDirs: mounts,
          });
      setLiveSid(handle.sessionId);
      navigate(`/s/${handle.sessionId}`);
      mutate("sessions");
      // 落盘附件 → 立刻 mutate fs:tree 让 ChatPage 接管时 Workspace 树看得到.
      // 内嵌图不落盘, 不刷.
      if (files.length > 0) {
        mutate(
          (key) => Array.isArray(key) && key[0] === "fs:tree",
          undefined,
          { revalidate: true },
        );
      }
      for await (const f of handle.frames) {
        setLiveFrames((prev) => appendFrame(prev, f));
        if (f.type === "stream_end") break;
      }
      mutate("sessions");
    } catch (err) {
      toast.error(`Send failed: ${String(err)}`);
      setSending(false);
      setSentPrompt(null);
      setSentAttachmentCount(0);
      setSentInlineImageCount(0);
      setPrompt(text);
      setDrafts(drafts);  // 失败时把 draft 还回 composer 让用户能 retry
      // 注: pastedImages 失败时不还原 — previewUrl blob 对应的 File 还在内存
      // 但 state 已清空, 用户必须重新粘贴. 简化处理 (重做成本低 + 避免管理 revoke).
    }
  }

  // 一进入发送态 → 立刻切到 ChatStream 视图 (像 Claude 桌面端一样, 不等后端 sid).
  // sid 没回来前传空串, 反正这阶段不会触发 workspace 授权弹窗 (那是 SDK tool_use 才会推);
  // 拿到 sid 后下面的 stream 循环会 setLiveSid, 顺带刷视图.
  if (sending || liveSid) {
    const now = new Date().toISOString();
    const pendingMeta: SessionMeta = {
      session_id: liveSid ?? "",
      title: sentPrompt,
      mounted_dirs: mounts,
      sandbox_dir: "",  // 真正 sandbox 路径在 sid 落地 + mutateMeta 后才知道; 此处占位
      created_at: now,
      last_active_at: now,
    };

    return (
      <div className="flex h-full min-h-0">
        <div className="min-w-0 flex-1">
          <ChatStream
            sessionId={liveSid ?? ""}
            streamedFrames={liveFrames}
            historyMessages={[]}
            localUserPrompt={sentPrompt}
            localAttachmentCount={sentAttachmentCount}
            localInlineImages={sentInlineImages}
            localInlineImageCount={sentInlineImageCount}
            onUserSend={() => {
              if (liveSid) navigate(`/s/${liveSid}`);
            }}
            inputDisabled
          />
        </div>
        <div className="hidden w-[340px] shrink-0 md:block">
          <RightPanel
            sessionId={liveSid ?? ""}
            meta={pendingMeta}
            history={[]}
            liveFrames={liveFrames}
            onMountsChanged={() => mutate("sessions")}
          />
        </div>
      </div>
    );
  }

  const canSend =
    !sending &&
    (prompt.trim().length > 0 || drafts.length > 0 || pastedImages.length > 0);

  return (
    <>
      <div className="relative h-full overflow-y-auto">
        <div className="mx-auto flex min-h-full max-w-[720px] flex-col justify-center px-6 py-16">
          {/* 标题区 — LoomMark 大版 + Fraunces italic 标语 + body 副标语 */}
          <div className="mb-10">
            <div className="flex items-start gap-4">
              <LoomMark size={44} active className="mt-1 shrink-0" />
              <h1 className="font-display text-[40px] italic font-normal leading-[1.05] tracking-[-0.015em] text-[color:var(--color-paper)]">
                Five threads,
                <br />
                <span className="inline-block translate-x-20">one weave.</span>
              </h1>
            </div>
            <p className="mt-4 max-w-[420px] text-[14px] leading-relaxed text-[color:var(--color-ink)]">
              Tell PentaLoom what to read, browse, run, search, or build —
              one prompt, five capabilities weaving in concert.
            </p>
          </div>

          {/* 输入卡片 — 16px 大圆角, focus 时钢蓝光圈, hover 微 lift */}
          <form
            onSubmit={onSubmit}
            onClick={(e) => {
              if ((e.target as HTMLElement).closest("button, textarea, input")) return;
              textareaRef.current?.focus();
            }}
            className="cursor-text rounded-[16px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] shadow-[0_1px_2px_rgba(20,30,50,0.03)] transition-all hover:shadow-[0_4px_16px_rgba(20,30,50,0.06)] focus-within:border-[color:var(--color-accent)] focus-within:shadow-[0_0_0_3px_rgba(61,90,128,0.12)]"
          >
            {(drafts.length > 0 || pastedImages.length > 0) && (
              <div className="flex flex-wrap gap-2 border-b border-[color:var(--color-line-soft)] px-3 pt-3 pb-2">
                {pastedImages.map((img) => (
                  <HoverCard key={img.id} openDelay={150} closeDelay={50}>
                    <HoverCardTrigger asChild>
                      <div
                        className="group flex items-center gap-2 rounded-[8px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-raised)] py-1 pl-1 pr-2 text-[12px] text-[color:var(--color-paper)]"
                      >
                        <img
                          src={img.previewUrl}
                          alt={img.name}
                          className="h-6 w-6 shrink-0 rounded-[4px] object-cover"
                        />
                        <span className="text-[color:var(--color-paper-dim)]">
                          {formatBytes(img.size)}
                        </span>
                        <button
                          type="button"
                          onClick={() => removeImage(img.id)}
                          className="rounded-[4px] text-[color:var(--color-paper-dim)] opacity-60 transition-colors hover:bg-[color:var(--color-line)] hover:text-[color:var(--color-paper)] hover:opacity-100"
                          title="Remove image"
                        >
                          <X size={12} />
                        </button>
                      </div>
                    </HoverCardTrigger>
                    <HoverCardContent className="w-auto p-2">
                      <div className="flex max-h-96 w-96 items-center justify-center overflow-hidden rounded-[6px] border border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-soft)]">
                        <img
                          src={img.previewUrl}
                          alt={img.name}
                          className="max-h-full max-w-full object-contain"
                        />
                      </div>
                      <div className="mt-2 px-0.5">
                        <p className="truncate text-[13px] font-semibold text-[color:var(--color-paper)]">
                          {img.name}
                        </p>
                        <p className="truncate text-[11px] text-[color:var(--color-paper-dim)]">
                          {img.file.type || "image"}
                        </p>
                      </div>
                    </HoverCardContent>
                  </HoverCard>
                ))}
                {drafts.map((d) => (
                  <div
                    key={d.id}
                    className="group flex items-center gap-2 rounded-[8px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-raised)] px-2 py-1 text-[12px] text-[color:var(--color-paper)]"
                    title={`${d.name} (${formatBytes(d.size)})`}
                  >
                    <Paperclip size={12} className="text-[color:var(--color-paper-dim)]" />
                    <span className="max-w-[180px] truncate">{d.name}</span>
                    <span className="text-[color:var(--color-paper-dim)]">
                      {formatBytes(d.size)}
                    </span>
                    <button
                      type="button"
                      onClick={() => removeDraft(d.id)}
                      className="ml-1 rounded-[4px] text-[color:var(--color-paper-dim)] opacity-60 transition-colors hover:bg-[color:var(--color-line)] hover:text-[color:var(--color-paper)] hover:opacity-100"
                      title="Remove attachment"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
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
              onPaste={onPaste}
              rows={2}
              placeholder="Begin a thread…"
              className="block w-full resize-none rounded-t-[16px] bg-transparent px-5 pt-5 pb-3 text-[15px] leading-relaxed text-[color:var(--color-paper)] placeholder:font-display placeholder:italic placeholder:text-[color:var(--color-ink-dim)] focus:outline-none"
            />

            <div className="flex items-center justify-between gap-3 border-t border-[color:var(--color-line-soft)] px-3 py-2.5">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                title="Attach files"
                className="flex h-10 w-10 items-center justify-center rounded-[8px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
              >
                <Paperclip size={17} />
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                onChange={onPickFiles}
              />

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

          {/* 卡片下方: 挂载入口 (白底 chip, 跟大输入卡片视觉同家族) + 已挂载目录 chips */}
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              disabled={mounts.length >= MAX_MOUNTS}
              className={cn(
                "inline-flex items-center gap-2 rounded-[8px] border bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] shadow-[0_1px_2px_rgba(20,30,50,0.03)] transition-all",
                mounts.length >= MAX_MOUNTS
                  ? "cursor-not-allowed border-[color:var(--color-line)] text-[color:var(--color-ink-dim)]"
                  : "border-[color:var(--color-line)] text-[color:var(--color-paper-dim)] hover:border-[color:var(--color-line-strong)] hover:text-[color:var(--color-paper)] hover:shadow-[0_2px_8px_rgba(20,30,50,0.06)]",
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
