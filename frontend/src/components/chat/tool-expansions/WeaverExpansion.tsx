// 统一处理 weaver 工具家族 — weave_skill / weave_app / weave_workflow /
// weave_app_write_file / weave_app_edit_file / weave_app_finalize /
// edit_weaver / delete_weaver / run_weaver. 不同子工具的 input 字段不同, 这里
// 做 best-effort 抽取 (kind / name / description / 主内容字段), 缺什么字段
// 退到 fallback Pane.
//
// 设计原则: 长期资产产物 (skill markdown / app manifest / workflow definition)
// 是这一族工具的核心交付; 把这块内容浮上来用 Pane 显示, 不要塞 JSON 兜底里.

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";

import { Field, MetaStrip, Pane, stringifyToolResult } from "./_shared";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

// 子工具识别 — 用 friendly suffix 而不是完整 mcp__ 前缀
type WeaverKind =
  | "weave_skill"
  | "weave_app"
  | "weave_app_revise"
  | "weave_app_write_file"
  | "weave_app_edit_file"
  | "weave_app_finalize"
  | "weave_workflow"
  | "weave_workflow_finalize"
  | "edit_weaver"
  | "delete_weaver"
  | "run_weaver"
  | "unknown";

function detectKind(toolName: string): WeaverKind {
  if (toolName.endsWith("__weave_skill")) return "weave_skill";
  // 注意顺序: 更具体的后缀先匹配 (revise / write_file / edit_file / finalize 都
  // 以 __weave_app 开头, 不先判会被 __weave_app 吞了).
  if (toolName.endsWith("__weave_app_revise")) return "weave_app_revise";
  if (toolName.endsWith("__weave_app_write_file")) return "weave_app_write_file";
  if (toolName.endsWith("__weave_app_edit_file")) return "weave_app_edit_file";
  if (toolName.endsWith("__weave_app_finalize")) return "weave_app_finalize";
  if (toolName.endsWith("__weave_app")) return "weave_app";
  if (toolName.endsWith("__weave_workflow_finalize")) return "weave_workflow_finalize";
  if (toolName.endsWith("__weave_workflow")) return "weave_workflow";
  if (toolName.endsWith("__edit_weaver")) return "edit_weaver";
  if (toolName.endsWith("__delete_weaver")) return "delete_weaver";
  if (toolName.endsWith("__run_weaver")) return "run_weaver";
  return "unknown";
}

// 不同子工具的"产物种类" — sidebar / status 上一致
function productKindFor(kind: WeaverKind): string | null {
  if (kind.startsWith("weave_app")) return "app";
  if (kind.startsWith("weave_workflow")) return "workflow";
  if (kind === "weave_skill") return "skill";
  return null;
}

export function WeaverExpansion({ use, result }: Props) {
  const kind = detectKind(use.name);
  const input = use.input as Record<string, unknown>;
  const product = productKindFor(kind);

  // 几个常见字段, 不一定每个子工具都有 — 取存在的
  const name = pickStr(input, ["name", "app_name", "workflow_name"]);
  const description = pickStr(input, ["description"]);
  const targetKind = pickStr(input, ["kind"]);  // edit_weaver / delete_weaver / run_weaver
  const relPath = pickStr(input, ["rel_path"]);

  // 主内容 — skill / workflow / app 的核心交付
  const content = pickStr(input, ["content"]);  // skill markdown / app file content
  const oldString = pickStr(input, ["old_string"]);
  const newString = pickStr(input, ["new_string"]);
  // app weave 时 manifest 跟 files 是 dict / list, 走 JSON
  const manifest = input.manifest;
  const definition = input.definition;
  const files = input.files;
  const args = input.args;

  return (
    <div className="space-y-2">
      <MetaStrip>
        {product && <Field label="kind">{product}</Field>}
        {targetKind && <Field label="target">{targetKind}</Field>}
        {name && (
          <Field label="name" mono>
            {name}
          </Field>
        )}
        {relPath && (
          <Field label="path" mono>
            {relPath}
          </Field>
        )}
      </MetaStrip>

      {description && (
        <div className="rounded-[5px] border border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-card)] px-3 py-2 text-[12.5px] leading-relaxed text-[color:var(--color-paper-dim)]">
          {description}
        </div>
      )}

      {/* 主内容 — content 优先 (skill markdown / app file body), 然后 manifest /
          definition / files (json 形式), 然后 edit 的 old/new diff. */}
      {content && <Pane label="Content">{truncate(content, 4000)}</Pane>}

      {(oldString || newString) && (
        <div className="space-y-1.5">
          {oldString && (
            <Pane label="Old" className="border-[color:var(--color-error)]/30">
              {truncate(oldString, 2000)}
            </Pane>
          )}
          {newString && (
            <Pane label="New" className="border-[color:var(--color-success)]/30">
              {truncate(newString, 2000)}
            </Pane>
          )}
        </div>
      )}

      {manifest !== undefined && manifest !== null && !content && (
        <Pane label="Manifest" highlight="json">
          {JSON.stringify(manifest, null, 2)}
        </Pane>
      )}

      {definition !== undefined && definition !== null && !content && (
        <Pane label="Definition" highlight="json">
          {JSON.stringify(definition, null, 2)}
        </Pane>
      )}

      {files !== undefined && files !== null && !content && (
        <Pane label="Files" highlight="json">
          {JSON.stringify(files, null, 2)}
        </Pane>
      )}

      {args !== undefined && args !== null && (
        <Pane label="Args" highlight="json">
          {JSON.stringify(args, null, 2)}
        </Pane>
      )}

      {result && (
        <Pane
          label={result.is_error ? "Error" : "Result"}
          error={result.is_error}
        >
          {stringifyToolResult(result.content)}
        </Pane>
      )}
    </div>
  );
}

function pickStr(
  input: Record<string, unknown>,
  keys: string[],
): string | null {
  for (const k of keys) {
    const v = input[k];
    if (typeof v === "string" && v.trim().length > 0) return v;
  }
  return null;
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max)}\n\n… (truncated, ${s.length - max} more chars)`;
}
