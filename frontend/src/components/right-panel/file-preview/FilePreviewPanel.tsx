// File preview 第三栏壳 — ChatPage 在 previewFile 不为 null 时挂载.
//
// 内部:
//   FilePreviewHeader  (关 / reveal)
//   FilePreview         (按 kind 路由到具体 renderer)
//
// 宽度 / 显隐由 ChatPage 控制, 这里只管内容.

import { useEffect } from "react";

import { usePreviewStore } from "@/lib/preview-store";

import { FilePreview } from "./FilePreview";

interface Props {
  sessionId: string;
}

export function FilePreviewPanel({ sessionId }: Props) {
  const previewFile = usePreviewStore((s) => s.previewFile);
  const closePreview = usePreviewStore((s) => s.closePreview);

  // ESC 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePreview();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [closePreview]);

  if (!previewFile) return null;

  return (
    <div className="flex h-full min-h-0 flex-col border-l border-[color:var(--color-line)] bg-[color:var(--color-bg-card)]">
      <FilePreview
        file={previewFile}
        sessionId={sessionId}
        onClose={closePreview}
      />
    </div>
  );
}
