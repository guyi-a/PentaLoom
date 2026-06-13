// web_search expansion — query meta + 命中结果列表卡 (title / url / snippet).
// 后端返 JSON 字符串 [{title, href, body}, ...] (见 agent/tools/search.py); 前端
// 解析失败退化到原文 Pane.

import { ExternalLink } from "lucide-react";

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";

import { Field, MetaStrip, Pane, stringifyToolResult, tryParseJson } from "./_shared";

interface SearchHit {
  title?: string;
  href?: string;
  body?: string;
}

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

export function WebSearchExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const query = String(input.query ?? "");
  const region = String(input.region ?? "both");
  const maxResults = typeof input.max_results === "number" ? input.max_results : null;
  const timelimit = typeof input.timelimit === "string" ? input.timelimit : null;
  const topic = typeof input.topic === "string" ? input.topic : null;

  const resultText = result ? stringifyToolResult(result.content) : "";
  const hits = result && !result.is_error ? tryParseJson<SearchHit[]>(resultText) : null;
  const validHits = Array.isArray(hits) ? hits : null;

  return (
    <div className="space-y-2">
      <MetaStrip>
        <Field label="query" mono>
          {query || "(empty)"}
        </Field>
        <Field label="region">{region}</Field>
        {maxResults !== null && <Field label="max">{maxResults}</Field>}
        {timelimit && <Field label="time">{timelimit}</Field>}
        {topic && <Field label="topic">{topic}</Field>}
      </MetaStrip>

      {result && result.is_error && (
        <Pane label="Error" error>
          {resultText}
        </Pane>
      )}

      {validHits && validHits.length === 0 && (
        <div className="rounded-[5px] border border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-card)] px-3 py-2 text-[12px] text-[color:var(--color-paper-dim)]">
          No results.
        </div>
      )}

      {validHits && validHits.length > 0 && (
        <div className="overflow-hidden rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)]">
          {validHits.map((hit, i) => (
            <SearchHitCard key={`${hit.href ?? i}`} hit={hit} index={i + 1} />
          ))}
        </div>
      )}

      {/* result 不是 error 但 parse 失败 — 退到原文 (e.g. "搜索 'xxx' 无结果" 的纯文本) */}
      {result && !result.is_error && !validHits && resultText && (
        <Pane label="Result">{resultText}</Pane>
      )}
    </div>
  );
}

function SearchHitCard({ hit, index }: { hit: SearchHit; index: number }) {
  const { title, href, body } = hit;
  const onClick = () => {
    if (href) window.open(href, "_blank", "noopener,noreferrer");
  };
  const hostname = href ? safeHostname(href) : null;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!href}
      className="group/hit block w-full border-b border-[color:var(--color-line-soft)] px-3 py-2 text-left transition-colors last:border-b-0 hover:bg-[color:var(--color-bg-soft)] disabled:cursor-default disabled:hover:bg-transparent"
    >
      <div className="flex items-start gap-2">
        <span className="shrink-0 select-none pt-0.5 font-mono text-[10.5px] text-[color:var(--color-ink-dim)]">
          {String(index).padStart(2, "0")}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 text-[12.5px] font-medium text-[color:var(--color-paper)]">
            <span className="truncate">{title || hostname || "(untitled)"}</span>
            {href && (
              <ExternalLink
                size={11}
                className="shrink-0 text-[color:var(--color-ink-dim)] opacity-0 transition-opacity group-hover/hit:opacity-100"
              />
            )}
          </div>
          {hostname && (
            <div className="mt-0.5 truncate font-mono text-[10.5px] text-[color:var(--color-thread-search)]">
              {hostname}
            </div>
          )}
          {body && (
            <div className="mt-1 line-clamp-2 text-[12px] leading-snug text-[color:var(--color-paper-dim)]">
              {body}
            </div>
          )}
        </div>
      </div>
    </button>
  );
}

function safeHostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
