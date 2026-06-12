// 文件预览主入口 — 按 ext 路由到具体 renderer.
//
// 流:
//   useFilePreview(path) 拉 metadata + (text 类型) textContent
//   classifyFile(ext, basename) → kind
//   按 kind switch 渲对应 renderer
//
// 不到 renderer 这层的 UI 状态 (loading / error / size 提示) 都在这里; renderer 内部只关心
// "拿到 content / url 后怎么显示".

import { ExternalLink, FileQuestion, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { PreviewFile } from "@/lib/preview-store";

import { useFilePreview } from "./useFilePreview";
import { classifyFile } from "./classifyFile";
import { FilePreviewHeader } from "./FilePreviewHeader";
import { CodePreview } from "./renderers/CodePreview";
import { DocxPreview } from "./renderers/DocxPreview";
import { ImagePreview } from "./renderers/ImagePreview";
import { MarkdownPreview } from "./renderers/MarkdownPreview";
import { MediaPreview } from "./renderers/MediaPreview";
import { NotebookPreview } from "./renderers/NotebookPreview";
import { PdfPreview } from "./renderers/PdfPreview";
import { PptxPreview } from "./renderers/PptxPreview";
import { TablePreview } from "./renderers/TablePreview";
import { UnsupportedPreview } from "./renderers/UnsupportedPreview";
import { XlsxPreview } from "./renderers/XlsxPreview";

interface Props {
  file: PreviewFile;
  sessionId: string;
  onClose: () => void;
}

export function FilePreview({ file, sessionId, onClose }: Props) {
  const { loading, error, meta, text } = useFilePreview(sessionId, file.path);

  const kind = meta ? classifyFile(meta.ext, meta.name) : null;

  // 错误归类: 后端返"path not found" / 403 / 越权时给清晰文案 + reveal/open 按钮兜底,
  // 不裸暴 JSON 错误. 保留原错误在折叠 details 里给排查.
  const errorInfo = error ? classifyError(error) : null;

  async function openWithSystem() {
    try {
      await api.openFile({ sessionId, path: file.path });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <FilePreviewHeader
        file={file}
        sessionId={sessionId}
        size={meta?.size}
        ext={meta?.ext}
        onClose={onClose}
      />
      <div className="min-h-0 flex-1 overflow-hidden">
        {loading && (
          <div className="flex h-full items-center justify-center text-[color:var(--color-ink)]">
            <Loader2 size={14} className="animate-spin" />
          </div>
        )}
        {errorInfo && (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 py-8 text-center">
            <FileQuestion size={28} className="text-[color:var(--color-ink-dim)]" />
            <div className="font-display text-[13px] italic text-[color:var(--color-paper-dim)]">
              {errorInfo.title}
            </div>
            <div className="max-w-md font-mono text-[10.5px] text-[color:var(--color-ink-dim)]">
              {errorInfo.hint}
            </div>
            {errorInfo.canOpen && (
              <button
                type="button"
                onClick={openWithSystem}
                className="mt-1 flex items-center gap-1.5 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
              >
                <ExternalLink size={11} />
                Open with system app
              </button>
            )}
            <details className="mt-2 max-w-md text-left font-mono text-[9.5px] text-[color:var(--color-ink-dim)]">
              <summary className="cursor-pointer">backend error</summary>
              <pre className="mt-1 whitespace-pre-wrap break-all">{error}</pre>
            </details>
          </div>
        )}
        {!loading && !error && meta && kind && (
          <>
            {kind === "code" && text && (
              <CodePreview content={text} fileName={meta.name} />
            )}
            {kind === "markdown" && text && (
              <MarkdownPreview content={text} />
            )}
            {kind === "image" && (
              <ImagePreview sessionId={sessionId} path={meta.path} />
            )}
            {kind === "pdf" && (
              <PdfPreview sessionId={sessionId} path={meta.path} />
            )}
            {(kind === "video" || kind === "audio") && (
              <MediaPreview sessionId={sessionId} path={meta.path} kind={kind} />
            )}
            {kind === "table" && text && (
              <TablePreview content={text} ext={meta.ext} />
            )}
            {kind === "notebook" && text && (
              <NotebookPreview content={text} />
            )}
            {kind === "docx" && (
              <DocxPreview sessionId={sessionId} path={meta.path} />
            )}
            {kind === "xlsx" && (
              <XlsxPreview sessionId={sessionId} path={meta.path} />
            )}
            {kind === "pptx" && (
              <PptxPreview sessionId={sessionId} path={meta.path} />
            )}
            {kind === "unsupported" && (
              <UnsupportedPreview meta={meta} sessionId={sessionId} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

interface ErrorInfo {
  title: string;
  hint: string;
  canOpen: boolean;  // True 时显示 "Open with system app" 按钮; 路径越权这种没意义.
}

function classifyError(error: string): ErrorInfo {
  // useFilePreview 返的 err.message 是 fetch 抛的, 形如 "404 Not Found {detail: '...'}"
  // 或 "403 Forbidden {...}". 提取 status code 给清晰文案.
  if (error.includes("404") && error.includes("path not found")) {
    return {
      title: "文件不存在",
      hint:
        "ContextSection 里这条 chip 是历史记录, 文件可能已被移动或删除. 跨 session 的旧路径也会触发这个错误.",
      canOpen: false,
    };
  }
  if (error.includes("403") || error.includes("outside allowed")) {
    return {
      title: "路径越权",
      hint: "该文件不在当前 session 的 sandbox / mounted_dirs 内. 用 Workspace 区挂载父目录后再试.",
      canOpen: false,
    };
  }
  if (error.includes("400") && error.includes("binary")) {
    return {
      title: "非文本文件",
      hint: "这个 ext 看似文本但内容是二进制. 用系统 app 打开看实际内容.",
      canOpen: true,
    };
  }
  return {
    title: "预览加载失败",
    hint: "后端返了非预期错误. 详情见下方 backend error.",
    canOpen: true,
  };
}
