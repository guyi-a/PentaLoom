// 单行聚合 chip — 把一对 (tool_use, tool_result|null) 渲染成 krow-app 风的
// 单行 chip + 状态 icon + 可展开特化内容. 取代旧 FrameBlock 的 ToolUseBlock +
// ToolResultBlock 两块堆叠.
//
// 状态机:
//   hitl-pending    待用户审批 (HITL 工具 tool_use 出现且没 tool_result)
//   in-progress     普通工具 tool_use 出现, tool_result 还没到
//   success         result.is_error=false
//   failed          result.is_error=true
//
// 默认展开: hitl-pending / failed; 折叠: in-progress / success.
// 用户点过 chevron 之后 (userToggled !== null) 锁定用户选择, 状态变化不再覆盖.
//
// hitl-pending → success/failed 转换时 250ms 后自动折叠 (仅 userToggled === null).
// 给用户一瞬间瞥到"哦, 审批通过了" 再自然收起来.

import { useEffect, useRef, useState } from "react";
import { Check, ChevronRight, Loader2, ShieldAlert, XCircle } from "lucide-react";

import { ApprovalInfo, InlineApprovalBar } from "./FrameBlock";
import { BashExpansion } from "./tool-expansions/BashExpansion";
import { FileVerifyExpansion } from "./tool-expansions/FileVerifyExpansion";
import { GenericExpansion } from "./tool-expansions/GenericExpansion";
import { RunScriptExpansion } from "./tool-expansions/RunScriptExpansion";
import { friendlyToolName, oneLineSummary, threadColorForTool, toolIcon } from "@/lib/tool-meta";
import {
  BASH_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
  RUN_SCRIPT_TOOL_NAME,
  TOOLS_NEEDING_APPROVAL,
  type ToolResultFrame,
  type ToolUseFrame,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export interface ToolPair {
  use: ToolUseFrame;
  result: ToolResultFrame | null;
}

type ToolStatus = "in-progress" | "hitl-pending" | "success" | "failed";

interface Props {
  pair: ToolPair;
  pendingApproval?: boolean;
  sessionId?: string;
}

function computeStatus(pair: ToolPair, pendingApproval: boolean): ToolStatus {
  if (pendingApproval) return "hitl-pending";
  if (!pair.result) return "in-progress";
  return pair.result.is_error ? "failed" : "success";
}

function defaultOpenFor(status: ToolStatus): boolean {
  return status === "hitl-pending" || status === "failed";
}

export function ToolRow({ pair, pendingApproval, sessionId }: Props) {
  const { use } = pair;
  const isHitl = TOOLS_NEEDING_APPROVAL.includes(use.name);
  const showApproval = !!pendingApproval && isHitl && !!sessionId;
  const status = computeStatus(pair, showApproval);
  // Skill 工具只是 LLM 主动加载某个 skill 的信号 — input/result 没什么可看的,
  // 一行显示 "Skill · <name>" 就够; 强行不可展开, 省得用户点开后看到一堆没用的 JSON.
  const isSkill = use.name === "Skill";

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

  return (
    <div
      className={cn(
        "rounded-[5px] border-l-2 bg-[color:var(--color-bg-soft)] py-1.5 pl-3 pr-3 transition-colors",
        status === "hitl-pending" && "ring-1 ring-[color:var(--color-warn)]/30",
        status === "success" && "bg-[color:var(--color-success)]/5",
        status === "failed" && "bg-[color:var(--color-error)]/5",
      )}
      style={{ borderLeftColor: threadColor }}
    >
      <button
        type="button"
        onClick={() => {
          if (isSkill) return;
          setUserToggled(!open);
        }}
        className={cn(
          "flex w-full items-center gap-2 text-left text-[13px]",
          isSkill && "cursor-default",
        )}
      >
        <Icon size={13} className="shrink-0 text-[color:var(--color-ink)]" />
        <span className="font-mono text-[13px] font-medium text-[color:var(--color-paper)]">
          {display}
        </span>
        {summary && (
          <span
            className="truncate font-mono text-[12.5px] text-[color:var(--color-paper-dim)]"
            title={summary}
          >
            · {summary}
          </span>
        )}
        {/* 状态 chip — 永远靠右挨着 chevron, 一眼能看 */}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <StatusBadge status={status} />
          {!isSkill && (
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

      {open && !isSkill && (
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
  if (name === RUN_SCRIPT_TOOL_NAME) return <RunScriptExpansion {...pair} />;
  if (name === FILE_VERIFY_TOOL_NAME) return <FileVerifyExpansion {...pair} />;
  return <GenericExpansion {...pair} />;
}

function StatusBadge({ status }: { status: ToolStatus }) {
  if (status === "hitl-pending") {
    return (
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
  return (
    <Check
      size={13}
      className="text-[color:var(--color-success)]"
      aria-label="done"
    />
  );
}
