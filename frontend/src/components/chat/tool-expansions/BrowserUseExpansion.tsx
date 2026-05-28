// browser_use 展开: 复杂命令显示细节, 简单命令只显示结果.

import { parseBrowserCommand } from "@/lib/browser-command";
import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";
import { stringifyToolResult } from "./GenericExpansion";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

interface BrowserUseResultPayload {
  command?: string;
  output?: string;
  [key: string]: unknown;
}

export function BrowserUseExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const command = String(input.command ?? "").trim();
  const parsed = parseBrowserCommand(command);
  const raw = result ? stringifyToolResult(result.content) : "";
  const payload = parseResultPayload(raw);
  const output = friendlyOutput(parsed?.action, payload?.output ?? raw);
  const pageScript = parsed?.action === "eval" ? parsed.args.join(" ") : "";
  const showCommandDetails = !parsed?.action || pageScript || parsed.globalArgs.length > 0;

  return (
    <div className="space-y-2">
      {showCommandDetails && !parsed?.action && (
        <Pane label="Command">{command || "(empty)"}</Pane>
      )}

      {showCommandDetails && pageScript && <Pane label="Page Script">{pageScript}</Pane>}

      {showCommandDetails && parsed && parsed.globalArgs.length > 0 && (
        <Pane label="Global Flags">{parsed.globalArgs.join(" ")}</Pane>
      )}

      {result && (
        <Pane label={result.is_error ? "Error" : "Output"} error={result.is_error}>
          {output}
        </Pane>
      )}
    </div>
  );
}

function friendlyOutput(action: string | undefined, output: string): string {
  const trimmed = output.trim();
  if (!trimmed) return "";

  if (action === "close" && /^closed:\s*pl-[\w-]+\s*$/.test(trimmed)) {
    return "Browser closed.";
  }

  if (action === "open") {
    const url = /^url:\s*(.+)$/m.exec(trimmed)?.[1]?.trim();
    if (url) return `Opened ${url}.`;
  }

  return output;
}

function parseResultPayload(raw: string): BrowserUseResultPayload | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (value && typeof value === "object") return value as BrowserUseResultPayload;
  } catch {
    // Raw text fallback below.
  }
  return null;
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
