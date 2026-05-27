// run_python_script 的展开: script_path + args + stdout (result 字符串).

import { Code2 } from "lucide-react";

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";
import { stringifyToolResult } from "./GenericExpansion";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

export function RunScriptExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const scriptPath = String(input.script_path ?? "");
  const rawArgs = input.args;
  const args = Array.isArray(rawArgs) ? rawArgs.map((x) => String(x)) : [];
  const desc = String(input.description ?? "").trim();
  const out = result ? stringifyToolResult(result.content) : "";

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-[12.5px]">
        <Code2 size={13} className="text-[color:var(--color-ink)]" />
        <span
          className="truncate font-mono text-[color:var(--color-paper)]"
          title={scriptPath}
        >
          {scriptPath}
        </span>
        {args.map((a, i) => (
          <span
            key={i}
            className="rounded-[3px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-1.5 py-0.5 font-mono text-[11px] text-[color:var(--color-paper-dim)]"
          >
            {a}
          </span>
        ))}
      </div>
      {desc && (
        <div className="text-[12px] text-[color:var(--color-ink)]">{desc}</div>
      )}
      {out && (
        <pre
          className={
            "max-h-[420px] overflow-y-auto whitespace-pre-wrap break-all rounded-[4px] border bg-[color:var(--color-bg-deep)] p-2.5 font-mono text-[12px] leading-relaxed " +
            (result?.is_error
              ? "border-[color:var(--color-error)]/40 text-[color:var(--color-error)]"
              : "border-[color:var(--color-line)] text-[color:var(--color-paper-dim)]")
          }
        >
          {out}
        </pre>
      )}
    </div>
  );
}
