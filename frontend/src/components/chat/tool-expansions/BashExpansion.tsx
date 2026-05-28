// Bash 工具的展开: terminal 风 (深底单色字) — command + stdout/stderr 一段一段.
// 没有真的 stdout/stderr 拆分 (后端目前一坨返回), 但视觉上仍然按 terminal 排版.

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";
import { stringifyToolResult } from "./GenericExpansion";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

export function BashExpansion({ use, result }: Props) {
  const cmd = String((use.input as Record<string, unknown>).command ?? "").trim();
  const desc = String((use.input as Record<string, unknown>).description ?? "").trim();
  const out = result ? stringifyToolResult(result.content) : "";

  return (
    <div className="space-y-2">
      {desc && (
        <div className="text-[12px] text-[color:var(--color-ink)]">{desc}</div>
      )}
      <div className="rounded-[4px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] font-mono text-[12.5px] leading-relaxed">
        <div className="px-3 py-2 text-[color:var(--color-paper)]">
          <span className="select-none text-[color:var(--color-thread-computer)]">
            ${" "}
          </span>
          <span className="whitespace-pre-wrap break-all">{cmd || "(empty)"}</span>
        </div>
        {out && (
          <div
            className={
              "max-h-[420px] overflow-y-auto whitespace-pre-wrap break-all border-t border-[color:var(--color-line)] px-3 py-2 " +
              (result?.is_error
                ? "text-[color:var(--color-error)]"
                : "text-[color:var(--color-paper-dim)]")
            }
          >
            {out}
          </div>
        )}
      </div>
    </div>
  );
}
