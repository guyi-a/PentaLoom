// 单行聚合 chip — 把一对 (tool_use, tool_result|null) 渲染成单行 chip +
// 状态 icon + 可展开特化内容. 取代旧 FrameBlock 的 ToolUseBlock +
// ToolResultBlock 两块堆叠.
//
// 状态机:
//   hitl-pending    待用户审批 (HITL 工具 tool_use 出现且没 tool_result)
//   in-progress     普通工具 tool_use 出现, tool_result 还没到
//   success         result.is_error=false
//   failed          result.is_error=true
//
// 默认展开: hitl-pending; 折叠: in-progress / success / failed.
// 用户点过 chevron 之后 (userToggled !== null) 锁定用户选择, 状态变化不再覆盖.
//
// hitl-pending → success/failed 转换时 250ms 后自动折叠 (仅 userToggled === null).
// 给用户一瞬间瞥到"哦, 审批通过了" 再自然收起来.

import { useEffect, useRef, useState } from "react";
import { Check, ChevronRight, CircleStop, Eye, Loader2, ShieldAlert, XCircle } from "lucide-react";

import { ApprovalInfo, InlineApprovalBar } from "./FrameBlock";
import { BashExpansion } from "./tool-expansions/BashExpansion";
import { BrowserBridgeExpansion } from "./tool-expansions/BrowserBridgeExpansion";
import { BrowserUseExpansion } from "./tool-expansions/BrowserUseExpansion";
import { ComputerUseExpansion } from "./tool-expansions/ComputerUseExpansion";
import { FileVerifyExpansion } from "./tool-expansions/FileVerifyExpansion";
import { GenericExpansion } from "./tool-expansions/GenericExpansion";
import { InstallPythonLibsExpansion } from "./tool-expansions/InstallPythonLibsExpansion";
import { InvokeExpansion } from "./tool-expansions/InvokeExpansion";
import { RunScriptExpansion } from "./tool-expansions/RunScriptExpansion";
import { WeaverExpansion } from "./tool-expansions/WeaverExpansion";
import { WebFetchExpansion } from "./tool-expansions/WebFetchExpansion";
import { WebSearchExpansion } from "./tool-expansions/WebSearchExpansion";
import { usePreviewStore } from "@/lib/preview-store";
import {
  PATH_KEYS,
  basename,
  friendlyToolName,
  oneLineSummary,
  threadColorForTool,
  toolIcon,
} from "@/lib/tool-meta";
import { toolMetric } from "@/lib/tool-metrics";
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
  TOOLS_NEEDING_APPROVAL,
  WEB_FETCH_TOOL_NAME,
  WEB_SEARCH_TOOL_NAME,
  type ToolResultFrame,
  type ToolUseFrame,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export interface ToolPair {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

type ToolStatus = "in-progress" | "hitl-pending" | "success" | "failed" | "stopped";

interface Props {
  pair: ToolPair;
  pendingApproval?: boolean;
  sessionId?: string;
}

// 把 tool_result content 拍成纯字符串, 给"是否中断" 之类 marker 检测用.
function resultContentText(result: ToolResultFrame): string {
  const c = result.content;
  if (typeof c === "string") return c;
  if (!Array.isArray(c)) return "";
  return c
    .filter((x): x is Record<string, unknown> => x !== null && typeof x === "object")
    .map((x) => String(x.text ?? ""))
    .join("");
}

// SDK 给"用户主动拒绝"工具调用的 tool_result content marker — 该归 stopped 状态
// (灰色 CircleStop, 不是红 XCircle). 实测三个触发源都用同一套"rejected" 引导文案:
//   - stop 按钮 (SDK interrupt) — 给挂起的 tool_use 注 cancellation result
//   - 审批 deny (SDK HITL)
//   - 我们 can_use_tool 主动 deny — workspace.py 里写的 "用户拒绝执行 ..."
// 小写子串匹配, 任一命中即 stopped. 真工具报错 (network / api key 等) 一般不含
// 这些词, false positive 风险低. badge 文案统一用 "STOPPED" — 用户感受贴
// "我停了" / "我没让它跑", 不用 "REFUSED" (容易跟审批 deny 混).
const REFUSAL_MARKERS = [
  "interrupted",
  "rejected",
  "doesn't want to proceed",
  "用户拒绝",
];

function isRefusedResult(result: ToolResultFrame | null): boolean {
  if (!result || !result.is_error) return false;
  const text = resultContentText(result).toLowerCase();
  return REFUSAL_MARKERS.some((k) => text.includes(k.toLowerCase()));
}

function computeStatus(pair: ToolPair, pendingApproval: boolean): ToolStatus {
  if (pendingApproval) return "hitl-pending";
  if (!pair.result) return "in-progress";
  if (isRefusedResult(pair.result)) return "stopped";
  return pair.result.is_error ? "failed" : "success";
}

function defaultOpenFor(status: ToolStatus): boolean {
  return status === "hitl-pending";
}

export function ToolRow({ pair, pendingApproval, sessionId }: Props) {
  const { use } = pair;
  const isHitl = TOOLS_NEEDING_APPROVAL.includes(use.name);
  const showApproval = !!pendingApproval && isHitl && !!sessionId;
  const status = computeStatus(pair, showApproval);
  // Skill / Read 这类工具在折叠标题里已经足够表达含义, 展开只会露出冗余 JSON.
  const compactOnly = use.name === "Skill" || use.name === "Read";

  // null = 用户没碰过, 跟着状态机走; true/false = 用户手动锁定
  const [userToggled, setUserToggled] = useState<boolean | null>(null);
  // 用来在 hitl-pending → 完成转换时延迟一拍折叠 (仅 userToggled===null 时生效)
  const [autoCollapsed, setAutoCollapsed] = useState(false);
  const prevStatusRef = useRef<ToolStatus>(status);

  useEffect(() => {
    const prev = prevStatusRef.current;
    prevStatusRef.current = status;
    if (userToggled !== null) return;
    if (prev === "hitl-pending" && status !== "hitl-pending") {
      // 让 chip 闪一下 "✓ 审批通过" 再收起, 250ms 是 chevron transition 的整数倍
      const t = setTimeout(() => setAutoCollapsed(true), 250);
      return () => clearTimeout(t);
    }
    // 进入新一轮 (回到 in-progress 之类) — 清掉 autoCollapsed 让默认规则重新接管
    if (status === "in-progress" || status === "hitl-pending") {
      setAutoCollapsed(false);
    }
  }, [status, userToggled]);

  const baseOpen = defaultOpenFor(status) && !autoCollapsed;
  const open = userToggled ?? baseOpen;

  const Icon = toolIcon(use.name);
  const display = friendlyToolName(use.name);
  const summary = oneLineSummary(use.name, use.input);
  const threadColor = threadColorForTool(use.name);
  // metric strip — 工具产出指标 (e.g. "exit 0 · 12 行" / "8 results"). result
  // 还没到时返 null, 退回 StatusBadge 显示状态. failed 状态优先 StatusBadge,
  // 不走 metric (避免重复渲染 "error" / 红 X 两种).
  const metric = status === "failed" ? null : toolMetric(use, pair.result);

  // 补 Context 删掉后的快捷点开 — 看 input 里有没有可预览的绝对路径,
  // 有就在 row 右上角加 hover 出现的 preview 按钮.
  const previewablePath = extractPreviewablePath(use.input);
  const openPreview = usePreviewStore((s) => s.openPreview);

  // failed 时左竖条强制变 error 红, 覆盖 thread color (强信号: 视觉一眼分辨).
  const leftBorderColor =
    status === "failed" ? "var(--color-error)" : threadColor;

  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-[6px] border-l-[3px] bg-[color:var(--color-bg-soft)] py-2 pl-3.5 pr-3 shadow-[0_1px_2px_rgba(20,30,50,0.02)] transition-colors",
        status === "hitl-pending" && "ring-1 ring-[color:var(--color-warn)]/30",
        status === "success" && "bg-[color:var(--color-success)]/5",
        status === "failed" && "bg-[color:var(--color-error)]/5",
        status === "stopped" && "opacity-70",
      )}
      style={{ borderLeftColor: leftBorderColor }}
    >
      {/* 顶边状态条:
            - in-progress: shimmer 流动光带, "进度感"
            - hitl-pending: warn 实线, "等待审批" */}
      {status === "in-progress" && (
        <span
          aria-hidden
          className="tool-shimmer-bar pointer-events-none absolute inset-x-0 top-0 h-[2px]"
        />
      )}
      {status === "hitl-pending" && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-[2px] bg-[color:var(--color-warn)]"
        />
      )}

      <button
        type="button"
        onClick={() => {
          if (compactOnly) return;
          setUserToggled(!open);
        }}
        className={cn(
          "flex w-full items-center gap-2 text-left text-[13px]",
          compactOnly && "cursor-default",
        )}
      >
        {/* icon 色块 — thread color 8% bg + thread color icon, 一眼出工具品类 */}
        <span
          aria-hidden
          className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-[4px]"
          style={{
            backgroundColor: `color-mix(in srgb, ${threadColor} 12%, transparent)`,
            color: threadColor,
          }}
        >
          <Icon size={11} />
        </span>
        <span className="shrink-0 whitespace-nowrap font-mono text-[13px] font-medium text-[color:var(--color-paper)]">
          {display}
        </span>
        {summary && (
          <span
            className="min-w-0 truncate font-mono text-[12.5px] text-[color:var(--color-paper-dim)]"
            title={summary}
          >
            · {summary}
          </span>
        )}
        {/* 右侧: metric strip / status badge / preview / chevron 集中靠右 */}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {metric && (
            <span
              className="hidden font-mono text-[11px] tabular-nums text-[color:var(--color-ink)] sm:inline"
              title={metric}
            >
              {metric}
            </span>
          )}
          {previewablePath && (
            // span 假按钮 (HTML 不许 button 嵌套 button) — onPointerDown stopPropagation
            // 防触发外层 toggle, click 走 openPreview.
            <span
              role="button"
              tabIndex={0}
              title={`Preview ${basename(previewablePath)}`}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                openPreview({
                  path: previewablePath,
                  name: basename(previewablePath),
                });
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.stopPropagation();
                  openPreview({
                    path: previewablePath,
                    name: basename(previewablePath),
                  });
                }
              }}
              className="cursor-pointer rounded-[3px] p-0.5 text-[color:var(--color-ink-dim)] opacity-0 transition-opacity hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)] group-hover:opacity-100"
            >
              <Eye size={11} />
            </span>
          )}
          <StatusBadge status={status} compact={!!metric} />
          {!compactOnly && (
            <ChevronRight
              size={13}
              className={cn(
                "text-[color:var(--color-ink)] transition-transform",
                open && "rotate-90",
              )}
            />
          )}
        </span>
      </button>

      {open && !compactOnly && (
        <div className="mt-2 space-y-2">
          {showApproval ? (
            <ApprovalInfo name={use.name} input={use.input} />
          ) : (
            <ExpansionFor pair={pair} />
          )}
          {showApproval && sessionId && (
            <InlineApprovalBar
              toolName={use.name}
              sessionId={sessionId}
              toolUseId={use.id}
              input={use.input}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ExpansionFor({ pair }: { pair: ToolPair }) {
  const name = pair.use.name;
  if (name === BASH_TOOL_NAME) return <BashExpansion {...pair} />;
  if (name === BROWSER_BRIDGE_TOOL_NAME) return <BrowserBridgeExpansion {...pair} />;
  if (name === BROWSER_USE_TOOL_NAME) return <BrowserUseExpansion {...pair} />;
  if (name === COMPUTER_USE_TOOL_NAME) return <ComputerUseExpansion {...pair} />;
  if (name === RUN_SCRIPT_TOOL_NAME) return <RunScriptExpansion {...pair} />;
  if (name === FILE_VERIFY_TOOL_NAME) return <FileVerifyExpansion {...pair} />;
  if (name === INSTALL_LIBS_TOOL_NAME) return <InstallPythonLibsExpansion {...pair} />;
  if (name === WEB_SEARCH_TOOL_NAME || name === "WebSearch") return <WebSearchExpansion {...pair} />;
  if (name === WEB_FETCH_TOOL_NAME) return <WebFetchExpansion {...pair} />;
  if (
    name === INVOKE_APP_TOOL_NAME ||
    name === INVOKE_WORKFLOW_TOOL_NAME ||
    name === INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME
  )
    return <InvokeExpansion {...pair} />;
  // weaver 工具家族 — 一组共用 WeaverExpansion. 不依赖完整 mcp__ 前缀, 用 suffix
  // match 兜底 (覆盖 weave_skill / weave_app* / weave_workflow* / edit_weaver /
  // delete_weaver / run_weaver).
  if (
    name.endsWith("__weave_skill") ||
    name.endsWith("__weave_app") ||
    name.endsWith("__weave_app_revise") ||
    name.endsWith("__weave_app_write_file") ||
    name.endsWith("__weave_app_edit_file") ||
    name.endsWith("__weave_app_finalize") ||
    name.endsWith("__weave_workflow") ||
    name.endsWith("__weave_workflow_finalize") ||
    name.endsWith("__edit_weaver") ||
    name.endsWith("__delete_weaver") ||
    name.endsWith("__run_weaver")
  )
    return <WeaverExpansion {...pair} />;
  return <GenericExpansion {...pair} />;
}

// compact: metric strip 已经显示了产出文字, 这里不重复渲染 approval / stopped
// 的 label 字, 仅保留小 icon — 节省横向空间.
function StatusBadge({ status, compact }: { status: ToolStatus; compact?: boolean }) {
  if (status === "hitl-pending") {
    return compact ? (
      <ShieldAlert size={12} className="text-[color:var(--color-warn)]" aria-label="awaiting approval" />
    ) : (
      <span className="flex items-center gap-1 rounded-[3px] bg-[color:var(--color-warn)]/15 px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-warn)]">
        <ShieldAlert size={10} />
        approval
      </span>
    );
  }
  if (status === "in-progress") {
    return (
      <Loader2
        size={12}
        className="animate-spin text-[color:var(--color-accent)]"
        aria-label="in progress"
      />
    );
  }
  if (status === "failed") {
    return (
      <XCircle
        size={13}
        className="text-[color:var(--color-error)]"
        aria-label="failed"
      />
    );
  }
  if (status === "stopped") {
    return compact ? (
      <CircleStop size={12} className="text-[color:var(--color-ink)]" aria-label="stopped" />
    ) : (
      <span className="flex items-center gap-1 rounded-[3px] bg-[color:var(--color-bg-raised)] px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-ink)]">
        <CircleStop size={10} />
        stopped
      </span>
    );
  }
  return (
    <Check
      size={13}
      className="text-[color:var(--color-success)]"
      aria-label="done"
    />
  );
}

// 从 tool input 提取一个可预览的绝对路径 — 给 ToolRow hover preview 按钮用.
// 取 PATH_KEYS 里第一个非空字符串值, 必须 / 开头. PATH_KEYS 包含 file_path / path /
// script_path / output_path / notebook_path 等常见路径字段.
function extractPreviewablePath(
  input: Record<string, unknown> | undefined,
): string | null {
  if (!input) return null;
  for (const key of PATH_KEYS) {
    const v = input[key];
    if (typeof v === "string" && v.startsWith("/")) return v;
  }
  return null;
}
