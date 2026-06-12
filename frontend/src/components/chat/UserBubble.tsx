// 用户消息渲染单元 — 不只是单个气泡, 视觉上是右对齐的"消息组":
//   1. 图片缩略图各占一格 (独立气泡, 跟 iMessage 多媒体先于文字的发送顺序对齐)
//   2. 文本 / 附件 indicator 占一格 (现状卡片样式)
// 图片跟文本拆开后, 视觉上更像"先发图再发字"两条独立消息, 跟用户操作动机匹配.
//
// 交互:
//   - composer 阶段的 chip → hover 弹大图 (PromptInput / EmptyPage 自己实现)
//   - 已发出 user bubble 的缩略图 → **click** 弹 lightbox modal (Esc / 点 backdrop 关)
//   - 大图: max-h-[90vh] max-w-[90vw] object-contain, 看清细节用
//
// inlineImages: live 时 blob URL, 历史时 data URL — 同款 src 字段, UserBubble 只管渲.
// inlineImageCount: 仅 resume mid-turn 拿不到具体 src 时退回 "🖼️ N 张图片" 占位.

import { useState } from "react";
import { ImageIcon, Paperclip } from "lucide-react";

import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export function UserBubble({
  text,
  attachmentCount = 0,
  inlineImages,
  inlineImageCount = 0,
}: {
  text: string;
  attachmentCount?: number;
  inlineImages?: { src: string }[];
  inlineImageCount?: number;
}) {
  const hasText = text.length > 0;
  const hasAttachments = attachmentCount > 0;
  const images = inlineImages ?? [];
  const hasImageThumbs = images.length > 0;
  // 占位仅在 "有 count 但 list 拿不到具体 src" 时出 (e.g. resume mid-turn).
  const hasImagePlaceholder = !hasImageThumbs && inlineImageCount > 0;
  // 文本气泡 = 真有文本 / 附件 indicator / 图片占位 任一; 三者全无时不渲第二格.
  const hasTextBubble = hasText || hasAttachments || hasImagePlaceholder;

  return (
    <div className="flex flex-col items-end gap-1.5">
      {hasImageThumbs && (
        <div className="flex max-w-[80%] flex-wrap justify-end gap-1.5">
          {images.map((img, i) => (
            <ImageThumb key={i} src={img.src} />
          ))}
        </div>
      )}
      {hasTextBubble && (
        <div className="max-w-[80%] rounded-[8px] bg-[color:var(--color-accent)]/8 px-3.5 py-2 text-[14px] leading-relaxed text-[color:var(--color-paper)] ring-1 ring-[color:var(--color-accent)]/15">
          {hasImagePlaceholder && (
            <div
              className={cn(
                "flex items-center gap-1.5 text-[12px] italic text-[color:var(--color-paper)]/60",
                (hasAttachments || hasText) && "mb-1.5",
              )}
            >
              <ImageIcon className="h-3.5 w-3.5" />
              <span>{inlineImageCount} 张图片</span>
            </div>
          )}
          {hasAttachments && (
            <div
              className={cn(
                "flex items-center gap-1.5 text-[12px] italic text-[color:var(--color-paper)]/60",
                hasText && "mb-1.5",
              )}
            >
              <Paperclip className="h-3.5 w-3.5" />
              <span>{attachmentCount} 个文件</span>
            </div>
          )}
          {hasText && <div className="whitespace-pre-wrap">{text}</div>}
        </div>
      )}
    </div>
  );
}

// 单张图片缩略图 — 点击弹 lightbox. 缩略图 96×96 圆角带边框; lightbox 居中
// max-h-[90vh] max-w-[90vw], 点 backdrop / Esc 关 (Radix Dialog 自带行为).
function ImageThumb({ src }: { src: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          type="button"
          className="size-24 shrink-0 cursor-zoom-in overflow-hidden rounded-[8px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] transition-shadow hover:shadow-[0_2px_8px_rgba(20,30,50,0.12)]"
          title="点击查看大图"
        >
          <img
            src={src}
            alt="inline attachment"
            className="size-full object-cover"
          />
        </button>
      </DialogTrigger>
      <DialogContent
        className="cursor-zoom-out"
        onClick={() => setOpen(false)}
      >
        <img
          src={src}
          alt="inline attachment full"
          className="block max-h-[90vh] max-w-[90vw] rounded-[10px] object-contain shadow-[0_12px_40px_rgba(0,0,0,0.5)]"
        />
      </DialogContent>
    </Dialog>
  );
}
