// browser_bridge 展开: 简单 action 只显示结果, 复杂脚本显示 Page Script.

import { parseBrowserBridgeInput } from "@/lib/browser-bridge";
import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";
import { stringifyToolResult } from "./GenericExpansion";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

export function BrowserBridgeExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const info = parseBrowserBridgeInput(input);
  const raw = result ? stringifyToolResult(result.content) : "";
  const output = friendlyBridgeOutput(info?.action, raw);
  const showDetails = !!info?.detail && info.action === "execute_script";

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

function friendlyBridgeOutput(action: string | undefined, raw: string): { label: string; text: string } {
  const trimmed = raw.trim();
  if (!trimmed) return { label: "Output", text: "" };

  const parsed = parseJson(trimmed);
  if (action === "open_tab" && parsed && typeof parsed === "object") {
    const url = stringField(parsed, "url");
    return { label: "Output", text: url ? `Opened ${url}.` : "Tab opened." };
  }
  if (action === "close_tab" && parsed && typeof parsed === "object") {
    return { label: "Output", text: "Tab closed." };
  }
  if (action === "reload" && trimmed === "reloaded") {
    return { label: "Output", text: "Page reloaded." };
  }
  if (action === "focus_page") return { label: "Output", text: "Page focused." };
  if (action === "read_state") return { label: "Page State", text: raw };
  if (action === "extension_status") return { label: "Status", text: formatJson(parsed, raw) };
  if (action === "list_sessions") return { label: "Browsers", text: formatJson(parsed, raw) };
  if (action === "list_pages") return { label: "Pages", text: formatJson(parsed, raw) };

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

function stringField(value: object, key: string): string {
  const record = value as Record<string, unknown>;
  return typeof record[key] === "string" ? record[key] : "";
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
