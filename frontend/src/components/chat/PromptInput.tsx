// 底部输入条 — 跟 EmptyPage 那个起始卡片同款 (圆角卡 + 上 textarea + 下工具栏).
// 风格基线: Nordic Light, 单色描边, focus-within 描边变 accent + 微 ring.
//
// 键位:
//   Enter         → 发送        (主行为, 跟 Claude / ChatGPT 一致)
//   Shift+Enter   → 换行        (浏览器默认, 不拦)
//   ⌘/Ctrl+Enter  → 也发送      (兼容老习惯)
//
// 主按钮三态:
//   - 有输入或附件 (idle) → accent 底 + ArrowUp, 点击 submit
//   - 无输入无附件 (idle) → 灰底 + ArrowUp, disabled
//   - sending + onStop    → paper 反相底 + Square, 点击 onStop
//
// 附件 (M19 follow-up):
//   - Paperclip 触发 <input type="file" multiple> 选择本地文件
//   - 已选文件以 chip 列展示在 textarea 上方 (文件名 + size + X 删除)
//   - 文件本体 = browser File 对象, 不预上传; 点击 Send 时父组件把它跟 prompt
//     一起 POST 到 /chat/with-attachments multipart endpoint
//   - 上限校验在后端 (50MB / file, 100MB total, 10 files), 前端不重复
//
// 粘贴图片 (内嵌, 不落盘):
//   - textarea onPaste 拦截 clipboard image item → 转 File + previewUrl
//   - chip 用 thumbnail 预览, 跟 file chip 同行展示 (但语义不同 — 走独立 state)
//   - 发送时通过 onSend 第三参数 inlineImages 传给父组件; 父组件走 multipart
//     字段 inline_images, 后端转 base64 + 拼 Anthropic image content block
//   - 不上传到 sandbox/attachments/ 文件夹 — 走 SDK 多模态路径让 LLM 真"看到"图

import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
} from "react";
import { ArrowUp, Paperclip, Square, X } from "lucide-react";

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { cn } from "@/lib/utils";

// composer state — 跟 plan §6.1 DraftAttachment 对齐. id 用 randomUUID 让删除
// 时不依赖文件名/大小做 key (重名文件能各自存活).
export interface DraftAttachment {
  id: string;
  file: File;
  name: string;
  size: number;
}

// 粘贴的内嵌图片 — 跟 DraftAttachment 平行的状态 (但语义不同, 不落盘).
// previewUrl 是 URL.createObjectURL 生成的 blob URL, 删除 / 卸载组件时 revoke.
export interface PastedImage {
  id: string;
  file: File;
  name: string;     // 浏览器 paste 给的 generated 名字 (e.g. "image.png")
  size: number;
  previewUrl: string;
}

interface Props {
  onSend: (prompt: string, files: File[], inlineImages: File[]) => void;
  // 父提供时 + disabled=true → 主按钮变 stop, 点击中断当前 turn.
  // 父没提供时 disabled 走旧逻辑 (灰按钮 disabled).
  onStop?: () => void;
  disabled?: boolean;
  placeholder?: string;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function PromptInput({ onSend, onStop, disabled, placeholder }: Props) {
  const [value, setValue] = useState("");
  const [drafts, setDrafts] = useState<DraftAttachment[]>([]);
  const [pastedImages, setPastedImages] = useState<PastedImage[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 卸载组件时 revoke 所有 blob URL — 防止 long-lived previewUrl 泄漏.
  // 删 chip 跟 submit 时也 revoke 单个 (在对应 handler 里调).
  useEffect(() => {
    return () => {
      pastedImages.forEach((img) => URL.revokeObjectURL(img.previewUrl));
    };
    // 仅在 unmount 跑一次 — 中途的 pastedImages 变化在 removeImage / submit 里手动 revoke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function submit() {
    const t = value.trim();
    // 接受 "纯文本" / "纯附件" / "纯图片" / 混合; 都没有时 no-op.
    if (disabled) return;
    if (!t && drafts.length === 0 && pastedImages.length === 0) return;
    onSend(
      t,
      drafts.map((d) => d.file),
      pastedImages.map((p) => p.file),
    );
    setValue("");
    setDrafts([]);
    pastedImages.forEach((img) => URL.revokeObjectURL(img.previewUrl));
    setPastedImages([]);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

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
    // 重置 input 让同一文件能再次选 (chip 删除后再选回来)
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    if (disabled) return;
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
        // 浏览器 paste 给 generated name 通常是 "image.png", 不一定有意义,
        // 但可读. 后端 inline image 不落盘, name 仅展示用.
        name: f.name || `pasted-${Date.now()}.${(it.type.split("/")[1] || "png")}`,
        size: f.size,
        previewUrl: URL.createObjectURL(f),
      });
    }
    if (collected.length === 0) return;
    // 至少一张图 — 拦默认 (避免文件名 / base64 又被插进 textarea), 然后塞进 state
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

  const canSend =
    !disabled &&
    (value.trim().length > 0 || drafts.length > 0 || pastedImages.length > 0);
  const showStop = !!disabled && !!onStop;

  return (
    <div className="border-t border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-6 py-4">
      <div className="mx-auto max-w-[820px]">
        <div
          onClick={(e) => {
            if ((e.target as HTMLElement).closest("button, textarea, input")) return;
            textareaRef.current?.focus();
          }}
          className={cn(
            "cursor-text rounded-[16px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] shadow-[0_1px_2px_rgba(20,30,50,0.03)] transition-all hover:shadow-[0_4px_16px_rgba(20,30,50,0.06)] focus-within:border-[color:var(--color-accent)] focus-within:shadow-[0_0_0_3px_rgba(61,90,128,0.12)]",
            disabled && "opacity-70",
          )}
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
                        disabled={disabled}
                        className="rounded-[4px] text-[color:var(--color-paper-dim)] opacity-60 transition-colors hover:bg-[color:var(--color-line)] hover:text-[color:var(--color-paper)] hover:opacity-100 disabled:cursor-not-allowed"
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
                    disabled={disabled}
                    className="ml-1 rounded-[4px] text-[color:var(--color-paper-dim)] opacity-60 transition-colors hover:bg-[color:var(--color-line)] hover:text-[color:var(--color-paper)] hover:opacity-100 disabled:cursor-not-allowed"
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
            value={value}
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
              setValue(e.target.value)
            }
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            disabled={disabled}
            placeholder={placeholder ?? "Continue the thread…"}
            rows={2}
            className="block w-full resize-none rounded-t-[16px] bg-transparent px-5 pt-5 pb-3 text-[15px] leading-relaxed text-[color:var(--color-paper)] placeholder:font-display placeholder:italic placeholder:text-[color:var(--color-ink-dim)] focus:outline-none disabled:cursor-not-allowed"
          />
          <div className="flex items-center justify-between gap-3 border-t border-[color:var(--color-line-soft)] px-3 py-2.5">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled}
              title="Attach files"
              className="flex h-10 w-10 items-center justify-center rounded-[8px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)] disabled:cursor-not-allowed disabled:opacity-50"
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
              type="button"
              onClick={showStop ? onStop : submit}
              disabled={!showStop && !canSend}
              title={showStop ? "Stop generating" : "Send (Enter)"}
              className={cn(
                "flex h-10 w-10 items-center justify-center rounded-[8px] transition-colors",
                showStop
                  ? "bg-[color:var(--color-paper)] text-[color:var(--color-bg-card)] hover:opacity-85"
                  : canSend
                    ? "bg-[color:var(--color-accent)] text-white hover:opacity-90"
                    : "cursor-not-allowed bg-[color:var(--color-bg-raised)] text-[color:var(--color-ink)]",
              )}
            >
              {showStop ? (
                <Square size={13} className="fill-current" />
              ) : (
                <ArrowUp size={17} />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
