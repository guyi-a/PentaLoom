// WebFetch expansion — URL meta + markdown 渲染的 content body.
// SDK 内置 WebFetch: input = {url, prompt}; result = 处理后的 markdown 文本.

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronUp, ExternalLink } from "lucide-react";

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";
import { cn } from "@/lib/utils";

import { Field, MetaStrip, Pane, stringifyToolResult } from "./_shared";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

// 折叠时最大高度 — 超出 240px 进 expand 按钮.
const COLLAPSED_MAX_HEIGHT = 240;

export function WebFetchExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const url = String(input.url ?? "");
  const prompt = typeof input.prompt === "string" ? input.prompt : "";
  const hostname = url ? safeHostname(url) : null;

  const text = result ? stringifyToolResult(result.content) : "";
  const isMarkdown = !result?.is_error && text.length > 0;

  const [expanded, setExpanded] = useState(false);

  return (
    <div className="space-y-2">
      <MetaStrip>
        <Field label="url" mono>
          {url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-[color:var(--color-thread-search)] hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {hostname || url}
              <ExternalLink size={10} />
            </a>
          ) : (
            "(empty)"
          )}
        </Field>
      </MetaStrip>

      {prompt && (
        <div className="rounded-[5px] border border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-card)] px-3 py-2 text-[12px] leading-relaxed text-[color:var(--color-paper-dim)]">
          <span className="text-[10.5px] uppercase tracking-wide text-[color:var(--color-ink)]">
            Prompt
          </span>
          <span className="mx-2 text-[color:var(--color-line-strong)]">·</span>
          {prompt}
        </div>
      )}

      {result?.is_error && (
        <Pane label="Error" error>
          {text}
        </Pane>
      )}

      {isMarkdown && (
        <div className="rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)]">
          <div
            className={cn(
              "prose-loom prose-sm relative px-3 py-2 text-[13px] leading-relaxed",
              !expanded && "overflow-hidden",
            )}
            style={!expanded ? { maxHeight: COLLAPSED_MAX_HEIGHT } : undefined}
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
            {!expanded && (
              <span
                aria-hidden
                className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-[color:var(--color-bg-card)] to-transparent"
              />
            )}
          </div>
          {/* 只有内容真的超 collapsed max 时才显 expand 按钮. heuristic: 简单
              估算 — 字符数 > 600 大概率超 240px. 真要精确, 用 ResizeObserver
              比较 scrollHeight, 但这是 over-engineering. */}
          {text.length > 600 && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setExpanded(!expanded);
              }}
              className="flex w-full items-center justify-center gap-1 border-t border-[color:var(--color-line-soft)] px-3 py-1.5 text-[11px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-soft)]"
            >
              {expanded ? (
                <>
                  <ChevronUp size={11} /> 收起
                </>
              ) : (
                <>
                  <ChevronDown size={11} /> 展开全部
                </>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function safeHostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
