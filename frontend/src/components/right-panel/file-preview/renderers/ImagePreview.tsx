// 图片预览 — 直接 <img src=/fs/preview/file?...>, contain 不裁切.

import { api } from "@/lib/api";

interface Props {
  sessionId: string;
  path: string;
}

export function ImagePreview({ sessionId, path }: Props) {
  const url = api.getPreviewFileUrl(sessionId, path);
  return (
    <div className="flex h-full min-h-0 items-center justify-center overflow-auto bg-[color:var(--color-bg-deep)] p-3">
      <img
        src={url}
        alt={path}
        className="max-h-full max-w-full object-contain"
      />
    </div>
  );
}
