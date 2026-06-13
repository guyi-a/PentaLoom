// 通用兜底 expansion — 没有专属 expansion 的工具 (Read/Write/Edit/Glob/Grep/
// Task/TodoWrite 等) 走这里. 早期版本是 input + result 两块裸 JSON pre, 看着
// 太密. 升级:
//
//  1. input 字段简单时 → MetaStrip (横向 label+value), 不强 JSON pre
//  2. input 字段复杂时 → JSON Pane (带 syntax highlight)
//  3. result 自动检测内容类型:
//       - 看着像 JSON → highlight="json"
//       - 长 shell 文本 → highlight="shell"
//       - 普通 → plain
//
// 不引 shiki — _shared 里的简单 regex highlighter 够用 (工具卡片快速扫读, 不是
// 代码编辑器, 完美 token 化是 over-engineering).

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";

import { Field, MetaStrip, Pane, stringifyToolResult, tryParseJson } from "./_shared";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

// 简单字段定义: string ≤ 200 / number / boolean / null. array 视为复杂 (除非空).
// 简单字段集合可以拍 MetaStrip; 含一个复杂字段就 fallback JSON.
function isSimpleValue(v: unknown): boolean {
  if (v == null) return true;
  if (typeof v === "boolean") return true;
  if (typeof v === "number") return true;
  if (typeof v === "string") return v.length <= 200;
  return false;
}

function isAllSimple(input: Record<string, unknown>): boolean {
  for (const v of Object.values(input)) {
    if (!isSimpleValue(v)) return false;
  }
  return true;
}

// 文本看着像 JSON: 第一个非空字符是 `{` 或 `[`.
function looksLikeJson(text: string): boolean {
  const t = text.trimStart();
  return t.length > 0 && (t[0] === "{" || t[0] === "[");
}

// 文本看着像 shell 输出: 含至少一个 ANSI escape / "$ " prompt / pip 进度行 / 等.
// 当前只用 "$ " prompt 做 hint, 误判面小.
function looksLikeShell(text: string): boolean {
  return /(^|\n)\s*[$#] /.test(text);
}

export function GenericExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const inputEntries = Object.entries(input);
  const allSimple = inputEntries.length > 0 && isAllSimple(input);

  const resultText = result ? stringifyToolResult(result.content) : "";
  const resultMode: "json" | "shell" | "plain" = !result
    ? "plain"
    : looksLikeJson(resultText)
      ? "json"
      : looksLikeShell(resultText)
        ? "shell"
        : "plain";

  return (
    <div className="space-y-2">
      {/* input 块 — 简单字段拍 strip, 复杂走 JSON */}
      {inputEntries.length > 0 &&
        (allSimple ? (
          <MetaStrip>
            {inputEntries.map(([k, v]) => (
              <Field key={k} label={k} mono={typeof v === "string"}>
                {formatSimpleValue(v)}
              </Field>
            ))}
          </MetaStrip>
        ) : (
          <Pane label="Input" highlight="json">
            {JSON.stringify(input, null, 2)}
          </Pane>
        ))}

      {/* result 块 */}
      {result && (
        <Pane
          label={result.is_error ? "Error" : "Result"}
          error={result.is_error}
          highlight={resultMode}
        >
          {/* JSON 模式: 试 parse + 重新 stringify(2) 让格式整齐. 失败 fallback 原文. */}
          {resultMode === "json"
            ? prettifyJson(resultText)
            : resultText}
        </Pane>
      )}
    </div>
  );
}

function formatSimpleValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  return String(v);
}

function prettifyJson(text: string): string {
  const parsed = tryParseJson(text);
  if (parsed == null) return text;
  try {
    return JSON.stringify(parsed, null, 2);
  } catch {
    return text;
  }
}

// 兼容 — 老 import 路径还在用 stringifyToolResult, re-export
export { stringifyToolResult } from "./_shared";
