// invoke_app / invoke_workflow / invoke_workflow_dynamic 共用一个 expansion.
// 三个 invoke 工具语义类似 — 调用一个产物 (app/workflow), 传 args, 拿 result.
//
// app   input: {name, invocation_id, args}
// wf    input: {name, args}
// wf_dy input: {name, args}  — 动态版返"plan markdown" 给主 agent 接管
//                              (跟静态版 result 形状不同, 但展示一致)

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";

import { Field, MetaStrip, Pane, stringifyToolResult } from "./_shared";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

type InvokeKind = "app" | "workflow" | "workflow_dynamic";

function detectKind(toolName: string): InvokeKind {
  if (toolName.endsWith("__invoke_workflow_dynamic")) return "workflow_dynamic";
  if (toolName.endsWith("__invoke_workflow")) return "workflow";
  return "app";  // invoke_app 或 fallback
}

export function InvokeExpansion({ use, result }: Props) {
  const kind = detectKind(use.name);
  const input = use.input as Record<string, unknown>;
  const target =
    typeof input.name === "string" ? input.name : "";
  const invocationId =
    typeof input.invocation_id === "string" ? input.invocation_id : "";
  const args = input.args;

  return (
    <div className="space-y-2">
      <MetaStrip>
        <Field label="kind">{kindLabel(kind)}</Field>
        {target && (
          <Field label="target" mono>
            {target}
          </Field>
        )}
        {invocationId && (
          <Field label="invocation" mono>
            {invocationId}
          </Field>
        )}
      </MetaStrip>

      {args !== undefined && args !== null && (
        <Pane label="Args" highlight="json">
          {JSON.stringify(args, null, 2)}
        </Pane>
      )}

      {result && (
        <Pane
          label={
            result.is_error
              ? "Error"
              : kind === "workflow_dynamic"
                ? "Plan"
                : "Result"
          }
          error={result.is_error}
        >
          {stringifyToolResult(result.content)}
        </Pane>
      )}
    </div>
  );
}

function kindLabel(k: InvokeKind): string {
  if (k === "workflow") return "workflow (static)";
  if (k === "workflow_dynamic") return "workflow (dynamic)";
  return "app";
}
