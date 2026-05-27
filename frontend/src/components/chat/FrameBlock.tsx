// 单帧渲染器: 把后端 SSE 一帧 (text/thinking/tool_use/tool_result/task_*/result/error)
// 渲染成一个"卡片". 整个聊天流就是 frames.map(FrameBlock).
//
// 设计原则:
// - 视觉密度按重要性分层: text 是主台词 (大字 + paper 色), thinking/tool 是边注 (小字 + ink 色)
// - tool_use / tool_result 用左侧色条标识所属"线" (file/app/browser/computer/search)
// - task_* 折叠成一行小 chip, 不抢戏
// - 工具输入 / 工具输出超过一定长度自动折叠, 点开看详情

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ChevronRight,
  Wrench,
  Brain,
  AlertCircle,
  CheckCircle2,
  Package,
  FileTerminal,
  Folder,
} from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { Frame } from "@/lib/types";
import {
  ALLOW_SESSION_TOOLS,
  BASH_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
  INSTALL_LIBS_TOOL_NAME,
  RUN_SCRIPT_TOOL_NAME,
  TOOLS_NEEDING_APPROVAL,
  WORKSPACE_PERMISSION_TOOL_NAME,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  frame: Frame;
  // 父组件算出来的"该 tool_use 当前在等用户审批" — 这里只在 Bash 工具上用到
  // (workspace 走独立 dialog, 不在 frame 内联). 由 ChatStream 通过 ToolUseBlock
  // 链路传下来.
  pendingApproval?: boolean;
  sessionId?: string;
}

export function FrameBlock({ frame, pendingApproval, sessionId }: Props) {
  switch (frame.type) {
    case "text":
      return <TextBlock text={frame.text} streaming={frame.streaming} />;
    case "text_delta":
    case "thinking_delta":
      // reducer 已把 deltas 合并进 streaming TextFrame / ThinkingFrame, 这里不渲染.
      return null;
    case "thinking":
      return <ThinkingBlock text={frame.text} streaming={frame.streaming} />;
    case "tool_use":
      return (
        <ToolUseBlock
          toolUseId={frame.id}
          name={frame.name}
          input={frame.input}
          pendingApproval={pendingApproval}
          sessionId={sessionId}
        />
      );
    case "tool_result":
      return (
        <ToolResultBlock
          content={frame.content}
          isError={frame.is_error}
        />
      );
    case "task_started":
      return (
        <TaskChip
          variant="started"
          taskId={frame.task_id}
          subagent={frame.subagent}
          description={frame.description}
        />
      );
    case "task_progress":
      return (
        <TaskChip
          variant="progress"
          taskId={frame.task_id}
          description={frame.description}
          lastTool={frame.last_tool}
        />
      );
    case "task_done":
      return (
        <TaskChip
          variant="done"
          taskId={frame.task_id}
          status={frame.status}
          summary={frame.summary}
        />
      );
    case "result":
      return (
        <ResultBlock
          text={frame.text}
          isError={frame.is_error}
          durationMs={frame.duration_ms}
          costUsd={frame.cost_usd}
          numTurns={frame.num_turns}
        />
      );
    case "error":
      return <ErrorBlock message={frame.message} />;
    case "stream_end":
      return null;
    default:
      // 兜底: 未知 frame, 不该有, 但别挂掉
      return (
        <div className="border border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] p-3 font-mono text-[11px] text-[color:var(--color-ink)]">
          unknown frame: {JSON.stringify(frame)}
        </div>
      );
  }
}

// ──── text — assistant 主台词 ────────────────────────────────
function TextBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className="prose-loom max-w-none text-[15px] leading-relaxed text-[color:var(--color-paper)]">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      {streaming && (
        <span
          aria-hidden
          className="caret ml-1 inline-block h-[7px] w-[7px] rounded-full bg-[color:var(--color-accent)] align-[1px]"
        />
      )}
    </div>
  );
}

// ──── thinking — 内心独白, 默认展开 ──────────────────────────
function ThinkingBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  // 默认展开 — 既然出现了 thinking 块就该让用户能直接看到内容. streaming 中字
  // 一个个流出来; 刷新 / 切走再回 / 历史回放也保持展开 (用户嫌长可手动收起).
  // 历史里 thinking 是上下文不是噪音, 内容也已经是模型 summarized, 不会过长.
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-[5px] border-l-2 border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-soft)] py-2 pl-3 pr-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 text-[13px] text-[color:var(--color-paper-dim)] hover:text-[color:var(--color-paper)]"
      >
        <Brain size={13} />
        <span>thinking</span>
        {streaming && (
          <span className="text-[color:var(--color-accent)]">·</span>
        )}
        <ChevronRight
          size={13}
          className={cn("transition-transform", open && "rotate-90")}
        />
      </button>
      {open && (
        <div className="mt-2 whitespace-pre-wrap text-[14px] leading-relaxed text-[color:var(--color-paper-dim)]">
          {text || (
            <span className="text-[12px] italic text-[color:var(--color-ink)]">
              (thinking redacted by upstream model provider)
            </span>
          )}
          {streaming && (
            <span
              aria-hidden
              className="caret ml-1 inline-block h-[6px] w-[6px] rounded-full bg-[color:var(--color-accent)] align-[1px]"
            />
          )}
        </div>
      )}
    </div>
  );
}

// ──── tool_use — agent 决定调用一个工具 ─────────────────────
function ToolUseBlock({
  toolUseId,
  name,
  input,
  pendingApproval,
  sessionId,
}: {
  toolUseId: string;
  name: string;
  input: Record<string, unknown>;
  pendingApproval?: boolean;
  sessionId?: string;
}) {
  // 任何 HITL 工具待审批时, 默认展开把"要做什么"清楚摆出来; 其它情况折叠.
  const showApproval =
    !!pendingApproval && TOOLS_NEEDING_APPROVAL.includes(name) && !!sessionId;
  const [open, setOpen] = useState(showApproval);
  const display = friendlyToolName(name);
  const threadColor = threadColorForTool(name);
  const oneLine = oneLineSummary(name, input);

  return (
    <div
      className={cn(
        "rounded-[5px] border-l-2 bg-[color:var(--color-bg-soft)] py-2 pl-3 pr-3",
        showApproval && "ring-1 ring-[color:var(--color-warn)]/30",
      )}
      style={{ borderLeftColor: threadColor }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 text-left text-[13px]"
      >
        <Wrench size={13} className="text-[color:var(--color-ink)]" />
        <span className="font-mono text-[13px] font-medium text-[color:var(--color-paper)]">
          {display}
        </span>
        {oneLine && (
          <span className="truncate font-mono text-[13px] text-[color:var(--color-paper-dim)]" title={oneLine}>
            · {oneLine}
          </span>
        )}
        {showApproval && (
          <span className="ml-1 rounded-[3px] bg-[color:var(--color-warn)]/15 px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-warn)]">
            awaiting approval
          </span>
        )}
        <ChevronRight
          size={13}
          className={cn(
            "ml-auto shrink-0 text-[color:var(--color-ink)] transition-transform",
            open && "rotate-90",
          )}
        />
      </button>
      {open && (
        // 审批中: 富信息块 (packages / script / path) 比 JSON 直观, 便于用户决策.
        // 非审批 / 历史回放: 仍用 JSON pre, 信息无损.
        showApproval ? (
          <ApprovalInfo name={name} input={input} />
        ) : (
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded-[4px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] p-2.5 font-mono text-[12px] leading-relaxed text-[color:var(--color-paper-dim)]">
            {JSON.stringify(input, null, 2)}
          </pre>
        )
      )}
      {showApproval && (
        <InlineApprovalBar
          toolName={name}
          sessionId={sessionId!}
          toolUseId={toolUseId}
          input={input}
        />
      )}
    </div>
  );
}

// 审批中的富信息块. 按 toolName 切渲染, 优先把用户拍板要看的字段亮出来.
function ApprovalInfo({
  name,
  input,
}: {
  name: string;
  input: Record<string, unknown>;
}) {
  if (name === BASH_TOOL_NAME) {
    const cmd = String(input.command ?? "").trim();
    const desc = String(input.description ?? "").trim();
    return (
      <div className="mt-2 space-y-2">
        <FieldBlock label="Command">
          <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-[12.5px] leading-relaxed text-[color:var(--color-paper)]">
            {cmd || "(empty)"}
          </pre>
        </FieldBlock>
        {desc && <FieldBlock label="What it does">{desc}</FieldBlock>}
      </div>
    );
  }

  if (name === INSTALL_LIBS_TOOL_NAME) {
    const rawLibs = input.libs;
    const libs = Array.isArray(rawLibs)
      ? rawLibs.map((x) => String(x)).filter((x) => x.trim())
      : [];
    const reason = String(input.reason ?? "").trim();
    return (
      <div className="mt-2 space-y-2">
        <FieldBlock label={`Packages (${libs.length})`} icon={Package}>
          <div className="flex flex-wrap gap-1.5">
            {libs.length === 0 ? (
              <span className="text-[11px] text-[color:var(--color-ink)]">
                (no packages requested)
              </span>
            ) : (
              libs.map((lib) => (
                <span
                  key={lib}
                  className="rounded-[3px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-2 py-0.5 font-mono text-[12px] text-[color:var(--color-paper)]"
                >
                  {lib}
                </span>
              ))
            )}
          </div>
        </FieldBlock>
        {reason && <FieldBlock label="Reason">{reason}</FieldBlock>}
      </div>
    );
  }

  if (name === RUN_SCRIPT_TOOL_NAME) {
    const scriptPath = String(input.script_path ?? "");
    const rawArgs = input.args;
    const args = Array.isArray(rawArgs) ? rawArgs.map((x) => String(x)) : [];
    const desc = String(input.description ?? "").trim();
    return (
      <div className="mt-2 space-y-2">
        <FieldBlock label="Script" icon={FileTerminal}>
          <span className="font-mono text-[12.5px] text-[color:var(--color-paper)]" title={scriptPath}>
            {scriptPath}
          </span>
        </FieldBlock>
        {args.length > 0 && (
          <FieldBlock label="Args">
            <div className="flex flex-wrap gap-1.5">
              {args.map((a, i) => (
                <span
                  key={i}
                  className="rounded-[3px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-2 py-0.5 font-mono text-[12px] text-[color:var(--color-paper)]"
                >
                  {a}
                </span>
              ))}
            </div>
          </FieldBlock>
        )}
        {desc && <FieldBlock label="What it does">{desc}</FieldBlock>}
      </div>
    );
  }

  if (name === WORKSPACE_PERMISSION_TOOL_NAME) {
    const path = String(input.path ?? "");
    const reason = String(input.reason ?? "").trim();
    return (
      <div className="mt-2 space-y-2">
        <FieldBlock label="Path" icon={Folder}>
          <span className="font-mono text-[12.5px] text-[color:var(--color-paper)]" title={path}>
            {path}
          </span>
        </FieldBlock>
        {reason && <FieldBlock label="Reason">{reason}</FieldBlock>}
      </div>
    );
  }

  // 兜底 — 不在 HITL_TOOL_NAMES 集合里, 不该走到这里, 但别挂掉
  return (
    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded-[4px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] p-2.5 font-mono text-[12px] leading-relaxed text-[color:var(--color-paper-dim)]">
      {JSON.stringify(input, null, 2)}
    </pre>
  );
}

function FieldBlock({
  label,
  icon: Icon,
  children,
}: {
  label: string;
  icon?: typeof Package;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-ink)]">
        {Icon && <Icon size={11} />}
        <span>{label}</span>
      </div>
      <div className="rounded-[4px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] px-2.5 py-1.5 text-[13px] leading-relaxed text-[color:var(--color-paper-dim)]">
        {children}
      </div>
    </div>
  );
}

// 内联审批条 — 所有 HITL 工具共用. 按 toolName 决定按钮组合 + 主按钮文案.
function InlineApprovalBar({
  toolName,
  sessionId,
  toolUseId,
  input,
}: {
  toolName: string;
  sessionId: string;
  toolUseId: string;
  input: Record<string, unknown>;
}) {
  const [busy, setBusy] = useState<
    null | "allow_once" | "allow_session" | "deny"
  >(null);

  // 主按钮文案: workspace/script "Allow" / "Run", 其它 "Allow once"
  const primaryLabel =
    toolName === WORKSPACE_PERMISSION_TOOL_NAME
      ? "Allow"
      : toolName === RUN_SCRIPT_TOOL_NAME
        ? "Run"
        : "Allow once";

  // allow_session 仅 Bash / install_libs 支持; 其它工具隐藏该按钮.
  const supportsAllowSession = ALLOW_SESSION_TOOLS.includes(toolName);

  // session 按钮是否可点 — Bash 需要非空 command, install_libs 需要非空 libs.
  // (允许后端做最终校验, 这里只是 UX 提示, 空了也别让用户点了再失败.)
  const sessionAllowed = (() => {
    if (!supportsAllowSession) return false;
    if (toolName === BASH_TOOL_NAME) {
      return !!String(input.command ?? "").trim();
    }
    if (toolName === INSTALL_LIBS_TOOL_NAME) {
      const libs = input.libs;
      return Array.isArray(libs) && libs.some((x) => String(x).trim());
    }
    if (toolName === FILE_VERIFY_TOOL_NAME) {
      return !!String(input.path ?? "").trim();
    }
    return false;
  })();

  const sessionLabel =
    toolName === BASH_TOOL_NAME
      ? "Allow same command (session)"
      : toolName === INSTALL_LIBS_TOOL_NAME
        ? "Allow same libs (session)"
        : toolName === FILE_VERIFY_TOOL_NAME
          ? "Allow same file (session)"
          : "Allow (session)";

  async function decide(decision: "allow_once" | "allow_session" | "deny") {
    if (busy) return;
    setBusy(decision);
    try {
      await api.respondPermission(toolUseId, {
        session_id: sessionId,
        decision,
      });
      if (decision === "allow_session") {
        toast.success("此组合本会话内免审");
      } else if (decision === "deny") {
        toast.info("已拒绝");
      }
      // 不主动隐藏 — pendingApproval prop 会因 tool_result 到达而变 false,
      // 卡片自然回退到普通形态. busy 状态在那之前显示为 "Sending…".
    } catch (err) {
      toast.error(`Response failed: ${String(err)}`);
      setBusy(null);
    }
  }

  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-[color:var(--color-line)] pt-2.5">
      <button
        type="button"
        onClick={() => decide("allow_once")}
        disabled={busy !== null}
        className={cn(
          "rounded-[5px] px-3 py-1.5 text-[12px] font-medium transition-colors",
          busy === "allow_once"
            ? "bg-[color:var(--color-accent)]/15 text-[color:var(--color-accent)]"
            : "bg-[color:var(--color-accent)] text-white hover:opacity-90",
          busy && busy !== "allow_once" && "cursor-not-allowed opacity-50",
        )}
      >
        {busy === "allow_once" ? "Sending…" : primaryLabel}
      </button>
      {supportsAllowSession && (
        <button
          type="button"
          onClick={() => decide("allow_session")}
          disabled={busy !== null || !sessionAllowed}
          title={
            sessionAllowed
              ? "本会话内同组合免审"
              : toolName === BASH_TOOL_NAME
                ? "命令为空, 无法加入白名单"
                : toolName === FILE_VERIFY_TOOL_NAME
                  ? "path 为空, 无法加入白名单"
                  : "libs 为空, 无法加入白名单"
          }
          className={cn(
            "rounded-[5px] border px-3 py-1.5 text-[12px] font-medium transition-colors",
            busy === "allow_session"
              ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]"
              : "border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] text-[color:var(--color-paper-dim)] hover:border-[color:var(--color-line-strong)] hover:text-[color:var(--color-paper)]",
            (busy && busy !== "allow_session") || !sessionAllowed
              ? "cursor-not-allowed opacity-50"
              : "",
          )}
        >
          {busy === "allow_session" ? "Sending…" : sessionLabel}
        </button>
      )}
      <button
        type="button"
        onClick={() => decide("deny")}
        disabled={busy !== null}
        className={cn(
          "ml-auto rounded-[5px] border px-3 py-1.5 text-[12px] font-medium transition-colors",
          busy === "deny"
            ? "border-[color:var(--color-error)] bg-[color:var(--color-error)]/10 text-[color:var(--color-error)]"
            : "border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] text-[color:var(--color-paper-dim)] hover:border-[color:var(--color-error)] hover:text-[color:var(--color-error)]",
          busy && busy !== "deny" && "cursor-not-allowed opacity-50",
        )}
      >
        {busy === "deny" ? "Sending…" : "Deny"}
      </button>
    </div>
  );
}

// ──── tool_result — 工具回执 ─────────────────────────────────
function ToolResultBlock({
  content,
  isError,
}: {
  content: unknown;
  isError: boolean;
}) {
  const text = stringifyToolResult(content);
  const isLong = text.length > 400;
  const [open, setOpen] = useState(!isLong);

  return (
    <div
      className={cn(
        "rounded-[5px] border-l-2 py-2 pl-3 pr-3",
        isError
          ? "border-[color:var(--color-error)] bg-[color:var(--color-error)]/5"
          : "border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-soft)]",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 text-left text-[13px]"
      >
        {isError ? (
          <AlertCircle size={13} className="text-[color:var(--color-error)]" />
        ) : (
          <CheckCircle2 size={13} className="text-[color:var(--color-ink)]" />
        )}
        <span
          className={cn(
            isError
              ? "text-[color:var(--color-error)]"
              : "text-[color:var(--color-paper-dim)]",
          )}
        >
          {isError ? "tool failed" : "tool result"}
        </span>
        <span className="font-mono text-[11px] text-[color:var(--color-ink)]">
          {isLong ? `${text.length} chars` : ""}
        </span>
        <ChevronRight
          size={13}
          className={cn(
            "ml-auto shrink-0 text-[color:var(--color-ink)] transition-transform",
            open && "rotate-90",
          )}
        />
      </button>
      {open && (
        <pre className="mt-2 max-h-[420px] overflow-y-auto whitespace-pre-wrap break-all font-mono text-[12.5px] leading-relaxed text-[color:var(--color-paper-dim)]">
          {text}
        </pre>
      )}
    </div>
  );
}

// ──── task_* — subagent 进度条 ───────────────────────────────
function TaskChip({
  variant,
  taskId,
  subagent,
  description,
  lastTool,
  status,
  summary,
}: {
  variant: "started" | "progress" | "done";
  taskId: string;
  subagent?: string | null;
  description?: string;
  lastTool?: string | null;
  status?: string;
  summary?: string | null;
}) {
  const label = variant === "started" ? "▸ task" : variant === "done" ? "✓ task" : "·";
  const color =
    variant === "done"
      ? "text-[color:var(--color-thread-browser)]"
      : "text-[color:var(--color-thread-app)]";

  return (
    <div className="flex flex-wrap items-baseline gap-2 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-2 font-mono text-[12px]">
      <span className={cn("font-medium", color)}>{label}</span>
      {subagent && (
        <span className="text-[color:var(--color-paper-dim)]">
          [{subagent}]
        </span>
      )}
      {description && (
        <span className="text-[color:var(--color-paper-dim)]">
          {description}
        </span>
      )}
      {lastTool && (
        <span className="text-[color:var(--color-ink)]">→ {lastTool}</span>
      )}
      {status && variant === "done" && (
        <span className="text-[color:var(--color-ink)]">[{status}]</span>
      )}
      {summary && (
        <span className="text-[color:var(--color-ink)]">
          · {truncate(summary, 80)}
        </span>
      )}
      <span className="ml-auto text-[color:var(--color-ink)]">
        {taskId.slice(0, 8)}
      </span>
    </div>
  );
}

// ──── result — 一轮 query() 结束 ─────────────────────────────
function ResultBlock({
  text,
  isError,
  durationMs,
  costUsd,
  numTurns,
}: {
  text: string | null;
  isError: boolean;
  durationMs: number | null;
  costUsd: number | null;
  numTurns: number | null;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-baseline gap-3 border-t border-[color:var(--color-line)] pt-2.5 font-mono text-[11px]",
        isError
          ? "text-[color:var(--color-error)]"
          : "text-[color:var(--color-ink)]",
      )}
    >
      <span>end of turn</span>
      {numTurns !== null && <span>{numTurns} turns</span>}
      {durationMs !== null && <span>{(durationMs / 1000).toFixed(2)}s</span>}
      {costUsd !== null && <span>${costUsd.toFixed(4)}</span>}
      {text && (
        <span className="text-[color:var(--color-paper-dim)]">
          · {truncate(text, 100)}
        </span>
      )}
    </div>
  );
}

// ──── error — 红条 ───────────────────────────────────────────
function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-[5px] border border-[color:var(--color-error)] bg-[color:var(--color-error)]/10 px-3 py-2.5 text-[13px] text-[color:var(--color-error)]">
      <AlertCircle size={15} className="mt-0.5 shrink-0" />
      <span className="whitespace-pre-wrap">{message}</span>
    </div>
  );
}

// ──── helpers ────────────────────────────────────────────────

function friendlyToolName(name: string): string {
  // mcp__pentaloom__request_workspace_dir → "request workspace dir"
  if (name.startsWith("mcp__")) {
    return name.split("__").slice(2).join("·").replace(/_/g, " ");
  }
  return name;
}

function threadColorForTool(name: string): string {
  // 简单分类: read/write/edit/glob/grep → file 线
  // bash → computer 线; task → app 线; webfetch/websearch → search 线
  const n = name.toLowerCase();
  if (n.includes("bash")) return "var(--color-thread-computer)";
  if (n === "task" || n.startsWith("task")) return "var(--color-thread-app)";
  if (n.includes("fetch") || n.includes("search") || n.includes("web"))
    return "var(--color-thread-search)";
  if (n.includes("browser")) return "var(--color-thread-browser)";
  if (
    n.includes("read") ||
    n.includes("write") ||
    n.includes("edit") ||
    n.includes("glob") ||
    n.includes("grep")
  )
    return "var(--color-thread-file)";
  return "var(--color-line-strong)";
}

function oneLineSummary(name: string, input: Record<string, unknown>): string {
  // 对常见工具给一个一眼能看出"做什么"的摘要
  const n = name.toLowerCase();
  if (n.includes("read") && typeof input.file_path === "string")
    return input.file_path;
  if ((n.includes("write") || n.includes("edit")) && typeof input.file_path === "string")
    return input.file_path;
  if (n.includes("bash") && typeof input.command === "string")
    return truncate(input.command, 80);
  if (n.includes("glob") && typeof input.pattern === "string")
    return input.pattern;
  if (n.includes("grep") && typeof input.pattern === "string")
    return input.pattern;
  if (n === "task" && typeof input.description === "string")
    return input.description;
  if (typeof input.path === "string") return input.path;
  if (typeof input.url === "string") return input.url;
  return "";
}

function stringifyToolResult(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    // [{type:'text', text}, ...]
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

function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max - 1) + "…";
}
