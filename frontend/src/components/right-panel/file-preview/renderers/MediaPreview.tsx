// Video / Audio 预览. 后端 file endpoint 注入了正确 mime type, 浏览器原生 <video>/<audio>
// 直接 load. 没用 hls/dash 这种流式协议, 所以巨大文件 (>1GB) 会一次性请求 — 第一版限制
// 用户自己承担, 后端不切片.

import { api } from "@/lib/api";

interface Props {
  sessionId: string;
  path: string;
  kind: "video" | "audio";
}

export function MediaPreview({ sessionId, path, kind }: Props) {
  const url = api.getPreviewFileUrl(sessionId, path);

  if (kind === "video") {
    return (
      <div className="flex h-full min-h-0 items-center justify-center bg-[color:var(--color-bg-deep)] p-3">
        <video
          src={url}
          controls
          className="max-h-full max-w-full"
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 items-center justify-center bg-[color:var(--color-bg-deep)] p-6">
      <audio src={url} controls className="w-full max-w-md" />
    </div>
  );
}
