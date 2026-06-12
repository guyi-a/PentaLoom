// docx 预览 — docx-preview 库客户端真渲染.
//
// 后端 /fs/preview/file 返 docx mime + ArrayBuffer, 前端 fetch as ArrayBuffer 喂给
// docx-preview 的 renderAsync 直接渲到 div 内. 包含完整段落格式 / 标题 / 列表 /
// 表格 / 图片.
//
// Symbol/Wingdings PUA 字体修复: docx 列表项目符号 (•▪○) 在 Symbol 字体下是 PUA
// 字符 (0xF0xx 区段), 浏览器没装 Symbol 字体会渲成 □. 修法: 把 PUA 字符替换成
// 等价 Unicode (• → •), 并把 Symbol/Wingdings font-family 改成 inherit.

import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";

interface Props {
  sessionId: string;
  path: string;
}

// 常见 PUA 字符 → Unicode 等价. krow 趟过的坑 — Symbol/Wingdings 字体在 .docx 列表
// 项目符号里很常见, 不修浏览器渲染成 tofu.
const SYMBOL_PUA_MAP: Record<number, string> = {
  0xf0b7: "•", // bullet •
  0xf0a7: "▪", // small black square
  0xf0a8: "■", // black square
  0xf0fc: "✔", // check mark
  0xf0fb: "✔",
  0xf06f: "○", // white circle
  0xf0fe: "☑", // ballot box with check
  0xf071: "●", // black circle
  0xf0a1: "●",
  0xf076: "❖",
  0xf0d8: "▲",
  0xf0e0: "✉",
  0xf0e8: "◆",
};

const SYMBOL_FONT_RE = /symbol|wingdings/i;

function escCssChar(ch: string): string {
  const code = ch.codePointAt(0);
  return code === undefined ? "" : "\\" + code.toString(16) + " ";
}

function fixSymbolChars(container: HTMLElement) {
  // 1) 文本节点里的 PUA 字符
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let node: Text | null;
  while ((node = walker.nextNode() as Text | null)) {
    const span = node.parentElement;
    if (!span || !SYMBOL_FONT_RE.test(span.style.fontFamily)) continue;

    let replaced = false;
    const text = node.nodeValue ?? "";
    const chars = Array.from(text);
    const mapped = chars.map((ch) => {
      const code = ch.codePointAt(0);
      if (code === undefined) return ch;
      const sub = SYMBOL_PUA_MAP[code];
      if (sub) {
        replaced = true;
        return sub;
      }
      // PUA 区段任何字符兜底成项目符号 — 避免 tofu.
      if (code >= 0xf000 && code <= 0xf0ff) {
        replaced = true;
        return "•";
      }
      return ch;
    });
    if (replaced) {
      node.nodeValue = mapped.join("");
      span.style.fontFamily = "inherit";
    }
  }

  // 2) <style> CSS 里的 \F0xx escape 序列 (列表项 ::before content)
  const styles = container.querySelectorAll("style");
  styles.forEach((style) => {
    let css = style.textContent ?? "";
    let changed = false;
    css = css.replace(/\\([Ff]0[0-9A-Fa-f]{2})/g, (_m, hex) => {
      const code = parseInt(hex, 16);
      const sub = SYMBOL_PUA_MAP[code];
      changed = true;
      return sub ? escCssChar(sub) : escCssChar("•");
    });
    css = css.replace(
      /font-family:\s*["']?(Symbol|Wingdings\d?)["']?/gi,
      () => {
        changed = true;
        return "font-family: inherit";
      },
    );
    if (changed) style.textContent = css;
  });
}

export function DocxPreview({ sessionId, path }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    if (containerRef.current) containerRef.current.innerHTML = "";

    (async () => {
      try {
        const url = api.getPreviewFileUrl(sessionId, path);
        const res = await fetch(url);
        if (!res.ok) {
          throw new Error(`${res.status} ${res.statusText}`);
        }
        const buffer = await res.arrayBuffer();
        if (cancelled) return;

        // 动态 import docx-preview — 重库, 只在用户真预览 docx 时拉
        const { renderAsync } = await import("docx-preview");
        if (cancelled || !containerRef.current) return;

        await renderAsync(buffer, containerRef.current, undefined, {
          inWrapper: false,
          ignoreWidth: true,
          ignoreHeight: true,
          breakPages: false,
          renderHeaders: false,
          renderFooters: false,
          renderFootnotes: true,
          renderEndnotes: true,
          useBase64URL: true,
        });
        if (cancelled) return;
        fixSymbolChars(containerRef.current);
        setLoading(false);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "docx 加载失败");
        setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
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
          docx 预览失败: {error}
        </div>
        <button
          type="button"
          onClick={openWithSystem}
          className="flex items-center gap-1.5 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
        >
          <ExternalLink size={11} />
          Open with Word
        </button>
      </div>
    );
  }

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-[color:var(--color-bg-card)]/80">
          <Loader2 size={14} className="animate-spin text-[color:var(--color-ink)]" />
        </div>
      )}
      <style>{`
        /* docx-preview 渲出来的内容 — Symbol/Wingdings 字体 fallback 到系统字体 */
        @font-face { font-family: Symbol;     src: local('Arial Unicode MS'), local('Arial'); }
        @font-face { font-family: Wingdings;  src: local('Arial Unicode MS'), local('Arial'); }
        @font-face { font-family: Wingdings2; src: local('Arial Unicode MS'), local('Arial'); }
        @font-face { font-family: Wingdings3; src: local('Arial Unicode MS'), local('Arial'); }
        .pl-docx-container p[class*="num"]::before {
          font-family: inherit !important;
        }
      `}</style>
      <div
        ref={containerRef}
        className="pl-docx-container scrollbar-hidden min-h-0 flex-1 overflow-auto px-5 py-4 text-[13px] leading-relaxed [&_table]:border-collapse [&_table]:w-full [&_td]:border [&_td]:border-[color:var(--color-line)] [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-[color:var(--color-line)] [&_th]:px-2 [&_th]:py-1 [&_img]:max-w-full [&_img]:h-auto"
      />
    </div>
  );
}
