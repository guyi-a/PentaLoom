// PDF 预览 — <iframe>. 后端返 Content-Type: application/pdf + Content-Disposition: inline,
// 浏览器用内置 PDF viewer 渲染 (Chrome/Edge/Safari 都支持).

import { api } from "@/lib/api";

interface Props {
  sessionId: string;
  path: string;
}

export function PdfPreview({ sessionId, path }: Props) {
  const url = api.getPreviewFileUrl(sessionId, path);
  return (
    <iframe
      src={url}
      title="PDF preview"
      className="h-full w-full border-0 bg-[color:var(--color-bg-deep)]"
    />
  );
}
