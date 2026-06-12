// File preview 顶栏 — 文件名 + size/ext 摘要 + reveal/系统 app 打开 + 关闭.
//
// 关闭按钮调 closePreview, panel 卸载.
// reveal 按钮走原 api.openFile (跟 ContextSection chip 旧行为一致, 给用户兜底).

import { ExternalLink, X } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { PreviewFile } from "@/lib/preview-store";
import { cn } from "@/lib/utils";

interface Props {
  file: PreviewFile;
  sessionId: string;
  size?: number;
  ext?: string;
  onClose: () => void;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function FilePreviewHeader({ file, sessionId, size, ext, onClose }: Props) {
  async function openWithSystem() {
    try {
      await api.openFile({ sessionId, path: file.path });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-3 py-2">
      <div className="min-w-0 flex-1">
        <div
          title={file.path}
          className="truncate font-mono text-[12.5px] text-[color:var(--color-paper)]"
        >
          {file.name}
        </div>
        <div className="mt-0.5 truncate font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          {file.relativePath ?? file.path}
        </div>
      </div>
      {(size !== undefined || ext) && (
        <div className="tabular flex shrink-0 items-center gap-1.5 font-mono text-[9.5px] uppercase text-[color:var(--color-ink-dim)]">
          {ext && <span>{ext}</span>}
          {size !== undefined && <span>{fmtBytes(size)}</span>}
        </div>
      )}
      <button
        type="button"
        onClick={openWithSystem}
        title="Open with system app"
        className={cn(
          "shrink-0 rounded-[4px] p-1.5 text-[color:var(--color-ink)]",
          "hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]",
        )}
      >
        <ExternalLink size={14} />
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        title="Close preview (Esc)"
        className={cn(
          "shrink-0 rounded-[4px] p-1.5 text-[color:var(--color-ink)]",
          "hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]",
        )}
      >
        <X size={14} />
      </button>
    </div>
  );
}
