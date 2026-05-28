// 通用兜底 expansion: input + result 都用 JSON pre.
// 任何没有专属 expansion 的工具走这里 — Read/Write/Edit/Glob/Grep/Task/Todo* 等.

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

export function GenericExpansion({ use, result }: Props) {
  const inputStr = JSON.stringify(use.input, null, 2);
  return (
    <div className="space-y-2">
      <Pane label="Input">{inputStr}</Pane>
      {result && (
        <Pane label={result.is_error ? "Error" : "Result"} error={result.is_error}>
          {stringifyToolResult(result.content)}
        </Pane>
      )}
    </div>
  );
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

export function stringifyToolResult(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((b) => {
        if (b && typeof b === "object" && "text" in b)
          return String((b as { text: unknown }).text);
        return JSON.stringify(b);
      })
      .join("\n");
  }
  if (content == null) return "";
  return JSON.stringify(content, null, 2);
}
