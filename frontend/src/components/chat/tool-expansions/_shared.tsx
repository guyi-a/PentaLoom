// expansion 共用 helpers — meta strip / 结果 pane / 内容类型自动检测 + 简单 syntax
// highlight. 给 5 个新 expansion + GenericExpansion 升级一起用.
//
// syntax highlight: 不引 shiki — 简单 regex 给 JSON / URL / 数字加 token 颜色,
// ~ 50 行内. shiki 是 file-preview 里 Code renderer 用的 (那里值得), 工具调用
// 卡片量大, 每张都 shiki 性能 / 启动成本不划算.

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

// 把 tool_result content (可能是 string / 对象数组 / null) 拍成纯文本.
// 跟 GenericExpansion.stringifyToolResult 同款逻辑, 抽到 _shared 让所有 expansion
// 复用 (避免循环 import).
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

// 尝试 parse 字符串成 JSON. 失败返 null. 给 expansion 判断"result 是不是结构化"
// 的统一入口.
export function tryParseJson<T = unknown>(text: string): T | null {
  if (!text) return null;
  const trimmed = text.trim();
  if (
    !trimmed ||
    (trimmed[0] !== "{" && trimmed[0] !== "[")
  )
    return null;
  try {
    return JSON.parse(trimmed) as T;
  } catch {
    return null;
  }
}

// ────────────── MetaStrip — input meta 短列表 ──────────────
// 工具 input 通常少量字段, 没必要 JSON pre. 横向 strip 列出 label + value.
//
// 示例: <MetaStrip><Field label="query">react RSC</Field>
//                  <Field label="region">both</Field></MetaStrip>

export function MetaStrip({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-2 text-[12px]">
      {children}
    </div>
  );
}

export function Field({
  label,
  children,
  mono,
}: {
  label: string;
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-1.5 min-w-0">
      <span className="shrink-0 text-[10.5px] uppercase tracking-wide text-[color:var(--color-ink)]">
        {label}
      </span>
      <span
        className={cn(
          "min-w-0 truncate text-[color:var(--color-paper)]",
          mono && "font-mono text-[12px]",
        )}
      >
        {children}
      </span>
    </div>
  );
}

// ────────────── Pane — 大段 result / error 文本 ──────────────
// 跟原 GenericExpansion.Pane 一致, 这里抽出来给所有 expansion 用.

export function Pane({
  label,
  error,
  highlight,
  children,
  className,
}: {
  label?: string;
  error?: boolean;
  highlight?: "json" | "shell" | "plain";
  children: ReactNode;
  className?: string;
}) {
  // highlight 仅当 children 是字符串时生效
  const content =
    highlight && typeof children === "string"
      ? highlightTokens(children, highlight)
      : children;

  return (
    <div>
      {label && (
        <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-ink)]">
          {label}
        </div>
      )}
      <pre
        className={cn(
          "max-h-[420px] overflow-y-auto whitespace-pre-wrap break-all rounded-[4px] border bg-[color:var(--color-bg-deep)] p-2.5 font-mono text-[12px] leading-relaxed",
          error
            ? "border-[color:var(--color-error)]/40 text-[color:var(--color-error)]"
            : "border-[color:var(--color-line)] text-[color:var(--color-paper-dim)]",
          className,
        )}
      >
        {content}
      </pre>
    </div>
  );
}

// ────────────── 极简 syntax highlight ──────────────
// JSON: 给 "key": / "string" / 数字 / true/false/null 上色.
// shell: 给 $ prompt / -flag / 数字 上色.
// 不追求完美 — 工具卡片快速扫读, 不是代码编辑器.

function highlightTokens(
  text: string,
  mode: "json" | "shell" | "plain",
): ReactNode {
  if (mode === "plain") return text;

  // 简单 tokenize — 用一个全局 regex, 找到的命中染色, 剩下原样.
  // JSON: 字符串("..."), 数字, true/false/null, 关键字
  // shell: 行首 $ , -flags, 数字
  const tokenRe =
    mode === "json"
      ? // 顺序: 字符串 (含 escape 内部) → 数字 → 关键字 → 空白/其它
        /("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?)|\b(true|false|null)\b/g
      : // shell: $/# prompt → -flag → 数字
        /(^\s*[$#] )|(\s-{1,2}[\w-]+)|(\d+)/gm;

  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = tokenRe.exec(text)) !== null) {
    if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
    const matched = match[0];
    parts.push(
      <span key={match.index} style={{ color: tokenColor(matched, mode) }}>
        {matched}
      </span>,
    );
    lastIndex = match.index + matched.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function tokenColor(t: string, mode: "json" | "shell"): string {
  if (mode === "json") {
    if (t.startsWith('"')) {
      // 字符串 — 区分 key / value: caller 上下文不知, 一律给 string 色
      return "var(--color-thread-browser)";  // 雾绿
    }
    if (/^-?\d+(\.\d+)?$/.test(t)) return "var(--color-thread-computer)"; // 暖橙
    if (t === "true" || t === "false" || t === "null")
      return "var(--color-accent)"; // 钢蓝
  }
  if (mode === "shell") {
    const trimmed = t.trim();
    if (trimmed === "$" || trimmed === "#") return "var(--color-thread-computer)";
    if (trimmed.startsWith("-")) return "var(--color-accent)";
    if (/^\d+$/.test(trimmed)) return "var(--color-thread-computer)";
  }
  return "inherit";
}
