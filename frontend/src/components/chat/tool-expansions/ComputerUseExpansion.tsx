// computer_use 展开: 简单 action 只显示结果, set_value 显示输入值.

import { parseComputerUseInput } from "@/lib/computer-use";
import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";
import { stringifyToolResult } from "./GenericExpansion";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

export function ComputerUseExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const info = parseComputerUseInput(input);
  const raw = result ? stringifyToolResult(result.content) : "";
  const output = friendlyComputerOutput(info?.action, raw);
  const showDetails = !!info?.detail && info.action === "set_value";

  return (
    <div className="space-y-2">
      {!info && <Pane label="Input">{JSON.stringify(input, null, 2)}</Pane>}
      {showDetails && <Pane label={info.detailLabel}>{info.detail}</Pane>}
      {result && (
        <Pane label={result.is_error ? "Error" : output.label} error={result.is_error}>
          {output.text}
        </Pane>
      )}
    </div>
  );
}

function friendlyComputerOutput(action: string | undefined, raw: string): { label: string; text: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { label: "Output", text: "" };

  const parsed = parseJson(trimmed);
  if (action === "permissions") return { label: "Permissions", text: formatJson(parsed, raw) };
  if (action === "apps") return { label: "Apps", text: formatJson(parsed, raw) };
  if (action === "snapshot") return { label: "Snapshot", text: formatJson(parsed, raw) };
  if (action === "focus") return { label: "Output", text: "App focused." };
  if (action === "key") return { label: "Output", text: "Key sent." };
  if (action === "menu") return { label: "Output", text: "Menu action completed." };
  if (action === "press") return { label: "Output", text: "Element pressed." };
  if (action === "set_value") return { label: "Output", text: "Value set." };

  return { label: "Output", text: formatJson(parsed, raw) };
}

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function formatJson(parsed: unknown, fallback: string): string {
  return parsed == null ? fallback : JSON.stringify(parsed, null, 2);
}

function Pane({
  label,
  error,
  children,
}: {
  label: string;
  error?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-ink)]">
        {label}
      </div>
      <pre
        className={
          "max-h-[420px] overflow-y-auto whitespace-pre-wrap break-all rounded-[4px] border bg-[color:var(--color-bg-deep)] p-2.5 font-mono text-[12px] leading-relaxed " +
          (error
            ? "border-[color:var(--color-error)]/40 text-[color:var(--color-error)]"
            : "border-[color:var(--color-line)] text-[color:var(--color-paper-dim)]")
        }
      >
        {children}
      </pre>
    </div>
  );
}
