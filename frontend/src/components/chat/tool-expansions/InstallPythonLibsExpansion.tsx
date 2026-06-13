// install_python_libs expansion — 包列表 + 安装结果 / 失败原因.
// input: {libs: string[], reason: string}; result: pip install 输出.

import { Check, Package, X as XIcon } from "lucide-react";

import type { ToolResultFrame, ToolUseFrame } from "@/lib/types";

import { Field, MetaStrip, Pane, stringifyToolResult } from "./_shared";

interface Props {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

// 把 "pkg==1.2.3" / "pkg>=1.0" 拆成 (name, version_spec).
function splitPkgSpec(spec: string): { name: string; version: string | null } {
  const m = spec.match(/^([A-Za-z0-9_.\-[\]]+)\s*(.*)$/);
  if (!m) return { name: spec, version: null };
  const ver = m[2].trim();
  return { name: m[1], version: ver || null };
}

export function InstallPythonLibsExpansion({ use, result }: Props) {
  const input = use.input as Record<string, unknown>;
  const libs = Array.isArray(input.libs)
    ? input.libs.filter((x): x is string => typeof x === "string")
    : [];
  const reason = typeof input.reason === "string" ? input.reason : "";

  const isError = !!result?.is_error;
  const resultText = result ? stringifyToolResult(result.content) : "";

  return (
    <div className="space-y-2">
      <MetaStrip>
        <Field label="count">{libs.length}</Field>
        {reason && <Field label="reason">{reason}</Field>}
      </MetaStrip>

      {libs.length > 0 && (
        <div className="overflow-hidden rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)]">
          {libs.map((spec, i) => {
            const { name, version } = splitPkgSpec(spec);
            return (
              <div
                key={`${spec}-${i}`}
                className="flex items-center gap-2 border-b border-[color:var(--color-line-soft)] px-3 py-1.5 text-[12.5px] last:border-b-0"
              >
                {/* status icon: 全 turn 成功 = ✓ green; 失败 = ✗ red. 真要每包细化
                    需要 parse pip output, 当前不做 — install_python_libs 设计上
                    要么全成要么集体失败. */}
                {result == null ? (
                  <Package
                    size={11}
                    className="shrink-0 text-[color:var(--color-ink-dim)]"
                  />
                ) : isError ? (
                  <XIcon
                    size={11}
                    className="shrink-0 text-[color:var(--color-error)]"
                  />
                ) : (
                  <Check
                    size={11}
                    className="shrink-0 text-[color:var(--color-success)]"
                  />
                )}
                <span className="font-mono text-[12.5px] font-medium text-[color:var(--color-paper)]">
                  {name}
                </span>
                {version && (
                  <span className="font-mono text-[11.5px] text-[color:var(--color-ink)]">
                    {version}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {result?.is_error && (
        <Pane label="Error" error highlight="shell">
          {resultText}
        </Pane>
      )}
    </div>
  );
}
