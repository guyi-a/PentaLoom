// 拉文件 metadata + (text 类型) textContent. 抄 krow useFilePreview 简化版.
//
// 流:
//   1. GET /fs/preview/stat → meta
//   2. classifyFile(meta.ext, meta.name) → kind
//   3. 如果 kind 是 code/markdown → GET /fs/preview/text 拉内容
//      其他 (image/pdf) renderer 直接拼 file URL, 不走这里
//   4. 异常 (越权 / not found / binary 走 text) 都进 error
//
// 切 path / sessionId 时取消上一个请求 (cancelled flag).

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { FilePreviewMeta, TextPreviewResult } from "@/lib/types";

import { classifyFile } from "./classifyFile";

interface PreviewState {
  loading: boolean;
  error: string | null;
  meta: FilePreviewMeta | null;
  text: TextPreviewResult | null;
}

export function useFilePreview(sessionId: string, filePath: string): PreviewState {
  const [state, setState] = useState<PreviewState>({
    loading: true,
    error: null,
    meta: null,
    text: null,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, meta: null, text: null });

    (async () => {
      try {
        const meta = await api.getPreviewMeta(sessionId, filePath);
        if (cancelled) return;

        const kind = classifyFile(meta.ext, meta.name);
        const needsText =
          kind === "code" ||
          kind === "markdown" ||
          kind === "table" ||
          kind === "notebook";

        if (needsText && !meta.is_binary_guess) {
          const text = await api.getPreviewText(sessionId, filePath);
          if (cancelled) return;
          setState({ loading: false, error: null, meta, text });
        } else {
          setState({ loading: false, error: null, meta, text: null });
        }
      } catch (err) {
        if (cancelled) return;
        setState({
          loading: false,
          error: err instanceof Error ? err.message : "Unknown error",
          meta: null,
          text: null,
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [sessionId, filePath]);

  return state;
}
