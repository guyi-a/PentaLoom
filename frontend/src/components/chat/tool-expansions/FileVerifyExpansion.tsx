// file_verify 展开: path + (尝试 JSON.parse result, 列 fixes_applied / issues / warnings).

import { ShieldCheck } from "lucide-react";

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";
import { stringifyToolResult } from "./GenericExpansion";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

interface VerifyResult {
  ok?: boolean;
  fixes_applied?: string[];
  issues?: string[];
  warnings?: string[];
  [k: string]: unknown;
}

export function FileVerifyExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const path = String(input.path ?? "");
  const autofix = Boolean(input.autofix ?? true);
  const raw = result ? stringifyToolResult(result.content) : "";

  let parsed: VerifyResult | null = null;
  if (raw) {
    try {
      const v = JSON.parse(raw);
      if (v && typeof v === "object") parsed = v as VerifyResult;
    } catch {
      // 留 parsed=null, 下面回退到 raw <pre>
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-[12.5px]">
        <ShieldCheck size={13} className="text-[color:var(--color-ink)]" />
        <span
          className="truncate font-mono text-[color:var(--color-paper)]"
          title={path}
        >
          {path}
        </span>
        <span className="ml-auto rounded-[3px] border border-[color:var(--color-line)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-[color:var(--color-ink)]">
          {autofix ? "autofix" : "check-only"}
        </span>
      </div>

      {parsed ? (
        <div className="space-y-1.5 rounded-[4px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] p-2.5 text-[12.5px] text-[color:var(--color-paper-dim)]">
          {parsed.ok !== undefined && (
            <div className="flex items-center gap-2">
              <span className="text-[10px] uppercase text-[color:var(--color-ink)]">
                ok
              </span>
              <span
                className={
                  parsed.ok
                    ? "text-[color:var(--color-accent)]"
                    : "text-[color:var(--color-error)]"
                }
              >
                {String(parsed.ok)}
              </span>
            </div>
          )}
          <ListField label="fixes_applied" items={parsed.fixes_applied} />
          <ListField label="issues" items={parsed.issues} error />
          <ListField label="warnings" items={parsed.warnings} />
        </div>
      ) : (
        raw && (
          <pre
            className={
              "max-h-[420px] overflow-y-auto whitespace-pre-wrap break-all rounded-[4px] border bg-[color:var(--color-bg-deep)] p-2.5 font-mono text-[12px] " +
              (result?.is_error
                ? "border-[color:var(--color-error)]/40 text-[color:var(--color-error)]"
                : "border-[color:var(--color-line)] text-[color:var(--color-paper-dim)]")
            }
          >
            {raw}
          </pre>
        )
      )}
    </div>
  );
}

function ListField({
  label,
  items,
  error,
}: {
  label: string;
  items?: string[];
  error?: boolean;
}) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <div className="text-[10px] uppercase text-[color:var(--color-ink)]">
        {label}
      </div>
      <ul
        className={
          "ml-3 list-disc space-y-0.5 " +
          (error ? "text-[color:var(--color-error)]" : "")
        }
      >
        {items.map((it, i) => (
          <li key={i} className="break-all">
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}
