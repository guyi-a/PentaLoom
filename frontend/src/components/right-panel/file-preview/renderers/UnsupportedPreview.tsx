// Unsupported 兜底 — 显示文件 size + ext + "用系统 app 打开"按钮.
//
// 第二 PR 加 office (docx/xlsx/pptx) / media / table / notebook 后, 这里覆盖范围会缩小.

import { ExternalLink } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { FilePreviewMeta } from "@/lib/types";

interface Props {
  meta: FilePreviewMeta;
  sessionId: string;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function UnsupportedPreview({ meta, sessionId }: Props) {
  async function openWithSystem() {
    try {
      await api.openFile({ sessionId, path: meta.path });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-8 text-center">
      <div className="font-display text-[13px] italic text-[color:var(--color-ink)]">
        Preview not supported for {meta.ext ? `.${meta.ext}` : "this file type"}
      </div>
      <div className="font-mono text-[11px] text-[color:var(--color-ink-dim)]">
        {meta.name} · {fmtBytes(meta.size)}
      </div>
      <button
        type="button"
        onClick={openWithSystem}
        className="mt-2 flex items-center gap-1.5 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
      >
        <ExternalLink size={11} />
        Open with system app
      </button>
    </div>
  );
}
