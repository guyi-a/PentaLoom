// 工具产出指标 — chip 折叠态右侧 metric strip 的来源.
//
// 每个高频工具一个 (use, result) → string | null 函数. result 没到时返 null,
// 让 chip 退回到只显 input summary. 设计原则: 显"产出量" (8 results / 12 lines /
// +3 packages), 不重复 input (input 已经在 oneLineSummary 那边显).

import type { ToolResultFrame, ToolUseFrame } from "./types";
import {
  BASH_TOOL_NAME,
  BROWSER_BRIDGE_TOOL_NAME,
  BROWSER_USE_TOOL_NAME,
  COMPUTER_USE_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
  INSTALL_LIBS_TOOL_NAME,
  INVOKE_APP_TOOL_NAME,
  INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME,
  INVOKE_WORKFLOW_TOOL_NAME,
  RUN_SCRIPT_TOOL_NAME,
  WEAVE_APP_TOOL_NAME,
  WEAVE_SKILL_TOOL_NAME,
  WEAVE_WORKFLOW_TOOL_NAME,
  WEB_SEARCH_TOOL_NAME,
} from "./types";

// tool_result content 拍平为字符串. 跟 ToolRow.resultContentText 同款逻辑,
// 这里独立一份避免循环依赖.
function resultText(result: ToolResultFrame): string {
  const c = result.content;
  if (typeof c === "string") return c;
  if (!Array.isArray(c)) return "";
  return c
    .filter((x): x is Record<string, unknown> => x !== null && typeof x === "object")
    .map((x) => String(x.text ?? ""))
    .join("");
}

function countLines(text: string): number {
  if (!text) return 0;
  return text.split("\n").filter((l) => l.length > 0).length;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

type MetricFn = (use: ToolUseFrame, result: ToolResultFrame | null) => string | null;

const METRIC_MAP: Record<string, MetricFn> = {
  [BASH_TOOL_NAME]: (_, r) => {
    if (!r) return null;
    const text = resultText(r);
    const lines = countLines(text);
    return r.is_error
      ? `exit≠0 · ${lines} 行`
      : `exit 0 · ${lines} 行`;
  },

  [WEB_SEARCH_TOOL_NAME]: (_, r) => {
    if (!r) return null;
    if (r.is_error) return "search failed";
    // result content 通常是 markdown / json. 数 "[1] " "[2] " 这种 result 标号.
    const text = resultText(r);
    const m = text.match(/^\s*\[\d+\]/gm);
    if (m && m.length > 0) return `${m.length} results`;
    // fallback: 数 URL 个数
    const urls = text.match(/https?:\/\/\S+/g);
    if (urls && urls.length > 0) return `${urls.length} results`;
    return "search done";
  },

  WebSearch: (_, r) => METRIC_MAP[WEB_SEARCH_TOOL_NAME](_, r),

  WebFetch: (_, r) => {
    if (!r) return null;
    if (r.is_error) return "fetch failed";
    const text = resultText(r);
    return `${fmtBytes(new Blob([text]).size)} fetched`;
  },

  [INSTALL_LIBS_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    const libs = use.input?.libs;
    if (Array.isArray(libs)) {
      return r.is_error ? `install failed (${libs.length} pkgs)` : `+${libs.length} packages`;
    }
    return r.is_error ? "install failed" : "installed";
  },

  [RUN_SCRIPT_TOOL_NAME]: (_, r) => {
    if (!r) return null;
    const text = resultText(r);
    const lines = countLines(text);
    return r.is_error ? `error · ${lines} 行` : `done · ${lines} 行`;
  },

  [FILE_VERIFY_TOOL_NAME]: (_, r) => {
    if (!r) return null;
    if (r.is_error) return "verify failed";
    const text = resultText(r);
    // 后端返回里出现 "fixed N 个" / "no issues" 等文案的简单 detect
    const fixedMatch = text.match(/fixed\s+(\d+)/i);
    if (fixedMatch) return `${fixedMatch[1]} fixed`;
    if (/no\s+issues|all\s+clear|clean/i.test(text)) return "all clear";
    return "verified";
  },

  [BROWSER_BRIDGE_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    const action = typeof use.input?.action === "string" ? use.input.action : "";
    return r.is_error ? `${action} failed` : action || "done";
  },

  [BROWSER_USE_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    const cmd = typeof use.input?.command === "string" ? use.input.command : "";
    // 从 "open url" / "click X" 这种 cmd 字符串里取首词
    const verb = cmd.split(/\s+/)[0] || "browser";
    return r.is_error ? `${verb} failed` : `${verb} done`;
  },

  [COMPUTER_USE_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    const action = typeof use.input?.action === "string" ? use.input.action : "";
    return r.is_error ? `${action} failed` : action || "done";
  },

  [WEAVE_SKILL_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    if (r.is_error) return "weave failed";
    const skillName = typeof use.input?.name === "string" ? use.input.name : "";
    return skillName ? `${skillName} saved` : "skill saved";
  },

  [WEAVE_APP_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    if (r.is_error) return "weave failed";
    const appName = typeof use.input?.name === "string" ? use.input.name : "";
    return appName ? `${appName} drafted` : "app drafted";
  },

  [WEAVE_WORKFLOW_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    if (r.is_error) return "weave failed";
    const wfName = typeof use.input?.name === "string" ? use.input.name : "";
    return wfName ? `${wfName} drafted` : "workflow drafted";
  },

  [INVOKE_APP_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    if (r.is_error) return "invoke failed";
    const target = typeof use.input?.name === "string" ? use.input.name : "";
    return target ? `${target} ran` : "ran";
  },

  [INVOKE_WORKFLOW_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    if (r.is_error) return "invoke failed";
    const target = typeof use.input?.name === "string" ? use.input.name : "";
    return target ? `${target} ran` : "ran";
  },

  [INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME]: (use, r) => {
    if (!r) return null;
    if (r.is_error) return "invoke failed";
    const target = typeof use.input?.name === "string" ? use.input.name : "";
    return target ? `${target} planned` : "planned";
  },

  // 内置工具
  Read: (use) => {
    const path = typeof use.input?.file_path === "string" ? use.input.file_path : "";
    if (!path) return null;
    // Read 没 result 就给"路径", 有 result 就给行数
    return null;  // 折叠态用 oneLineSummary 的路径就够了
  },

  Write: (_, r) => {
    if (!r) return null;
    return r.is_error ? "write failed" : "written";
  },

  Edit: (_, r) => {
    if (!r) return null;
    return r.is_error ? "edit failed" : "edited";
  },

  TodoWrite: (use) => {
    const todos = use.input?.todos;
    if (!Array.isArray(todos)) return null;
    return `${todos.length} todos`;
  },

  Glob: (_, r) => {
    if (!r) return null;
    if (r.is_error) return "glob failed";
    const lines = countLines(resultText(r));
    return `${lines} match${lines === 1 ? "" : "es"}`;
  },

  Grep: (_, r) => {
    if (!r) return null;
    if (r.is_error) return "grep failed";
    const lines = countLines(resultText(r));
    return `${lines} match${lines === 1 ? "" : "es"}`;
  },
};

export function toolMetric(
  use: ToolUseFrame,
  result: ToolResultFrame | null,
): string | null {
  const fn = METRIC_MAP[use.name];
  if (!fn) return null;
  try {
    return fn(use, result);
  } catch {
    return null;
  }
}
