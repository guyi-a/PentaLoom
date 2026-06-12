// pptx 预览 — @aiden0z/pptx-renderer 客户端真渲染.
//
// 后端 /fs/preview/file 返 pptx mime + ArrayBuffer, 前端 fetch as ArrayBuffer 喂给
// PptxViewer 渲到 div. windowed list mode — 大量 slide 时按需渲染, 不一次性渲全.

import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";

interface Props {
  sessionId: string;
  path: string;
}

export function PptxPreview({ sessionId, path }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  // PptxViewer 实例 — 切换文件时要 destroy() 释放, 否则上一个 viewer 的 DOM 还挂着
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const viewerRef = useRef<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const abortController = new AbortController();
    setLoading(true);
    setError(null);

    if (viewerRef.current) {
      viewerRef.current.destroy();
      viewerRef.current = null;
    }
    if (containerRef.current) containerRef.current.innerHTML = "";

    (async () => {
      try {
        const url = api.getPreviewFileUrl(sessionId, path);
        const res = await fetch(url, { signal: abortController.signal });
        if (!res.ok) {
          throw new Error(`${res.status} ${res.statusText}`);
        }
        const buffer = await res.arrayBuffer();
        if (abortController.signal.aborted) return;

        // 动态 import — 重库 (~500KB), 只在用户真预览 pptx 时拉
        const { PptxViewer, RECOMMENDED_ZIP_LIMITS } = await import(
          "@aiden0z/pptx-renderer"
        );
        if (abortController.signal.aborted || !containerRef.current) return;

        const viewer = new PptxViewer(containerRef.current, {
          fitMode: "contain",
          scrollContainer: containerRef.current.parentElement ?? undefined,
          zipLimits: RECOMMENDED_ZIP_LIMITS,
        });
        viewerRef.current = viewer;

        await viewer.open(buffer, {
          renderMode: "list",
          listOptions: { windowed: true, batchSize: 4 },
          signal: abortController.signal,
        });

        if (!abortController.signal.aborted) setLoading(false);
      } catch (err) {
        if (abortController.signal.aborted) return;
        setError(err instanceof Error ? err.message : "pptx 加载失败");
        setLoading(false);
      }
    })();

    return () => {
      abortController.abort();
      if (viewerRef.current) {
        viewerRef.current.destroy();
        viewerRef.current = null;
      }
    };
  }, [sessionId, path]);

  async function openWithSystem() {
    try {
      await api.openFile({ sessionId, path });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <div className="text-[12px] text-[color:var(--color-error)]">
          pptx 预览失败: {error}
        </div>
        <button
          type="button"
          onClick={openWithSystem}
          className="flex items-center gap-1.5 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
        >
          <ExternalLink size={11} />
          Open with PowerPoint
        </button>
      </div>
    );
  }

  return (
    <div className="relative h-full min-h-0 overflow-auto bg-[color:var(--color-bg-deep)]">
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-[color:var(--color-bg-card)]/80">
          <Loader2 size={14} className="animate-spin text-[color:var(--color-ink)]" />
        </div>
      )}
      <div ref={containerRef} className="pl-pptx-container h-full w-full" />
    </div>
  );
}
