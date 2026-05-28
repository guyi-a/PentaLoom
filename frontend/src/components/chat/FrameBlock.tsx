// 单帧渲染器: 把后端 SSE 一帧 (text/thinking/task_*/result/error) 渲染成一个"卡片".
// tool_use / tool_result 不再走这里 — 配对成 ToolPair 由 <ToolRow> 渲染一行.
// 见 ChatStream.tsx 的 pairedFrames 派生.

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertCircle,
  Brain,
  ChevronRight,
  Folder,
  Package,
  FileTerminal,
  Type,
} from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { Frame } from "@/lib/types";
import {
  ALLOW_SESSION_TOOLS,
  BASH_TOOL_NAME,
  BROWSER_USE_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
  INSTALL_BROWSER_USE_TOOL_NAME,
  INSTALL_FONT_TOOL_NAME,
  INSTALL_LIBS_TOOL_NAME,
  RUN_SCRIPT_TOOL_NAME,
  WORKSPACE_PERMISSION_TOOL_NAME,
} from "@/lib/types";
import { truncate } from "@/lib/tool-meta";
import { cn } from "@/lib/utils";

interface Props {
  frame: Frame;
}

export function FrameBlock({ frame }: Props) {
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
    case "tool_result":
      // 现在由 ChatStream 配对成 ToolPair → <ToolRow>. 这里走到说明上游漏配对了,
      // 不该把 raw JSON 抛给用户, 静默吞掉.
      return null;
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
    case "permission_resolved":
      // 控制帧: 仅用于让 ChatStream 的 pendingApprovalIds reducer 知道审批已落定,
      // UI 上不渲染卡片 — 否则 "Allow → 卡片立刻消失" 的体验就被这块兜底破坏了.
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

// ──── ApprovalInfo / InlineApprovalBar / FieldBlock — 给 ToolRow 复用 ────

// 审批中的富信息块. 按 toolName 切渲染, 优先把用户拍板要看的字段亮出来.
export function ApprovalInfo({
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
      <div className="space-y-2">
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
      <div className="space-y-2">
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
      <div className="space-y-2">
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
      <div className="space-y-2">
        <FieldBlock label="Path" icon={Folder}>
          <span className="font-mono text-[12.5px] text-[color:var(--color-paper)]" title={path}>
            {path}
          </span>
        </FieldBlock>
        {reason && <FieldBlock label="Reason">{reason}</FieldBlock>}
      </div>
    );
  }

  if (name === INSTALL_FONT_TOOL_NAME) {
    const reason = String(input.reason ?? "").trim();
    return (
      <div className="space-y-2">
        <FieldBlock label="Font" icon={Type}>
          <span className="font-mono text-[12.5px] text-[color:var(--color-paper)]">
            Noto Sans SC (中文字体)
          </span>
        </FieldBlock>
        <FieldBlock label="Install path">
          <span className="text-[12.5px] text-[color:var(--color-paper-dim)]">
            macOS: brew --cask 优先, 失败兜底下载 ~/Library/Fonts/ ·
            Linux: ~/.local/share/fonts/ · Windows: %LOCALAPPDATA%/Microsoft/Windows/Fonts/
          </span>
        </FieldBlock>
        {reason && <FieldBlock label="Reason">{reason}</FieldBlock>}
        <div className="text-[11px] text-[color:var(--color-ink)]">
          一次性操作 · 下载约 10-20MB · 装完仅本机生效
        </div>
      </div>
    );
  }

  // 兜底 — 不在 HITL_TOOL_NAMES 集合里, 不该走到这里, 但别挂掉
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-[4px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] p-2.5 font-mono text-[12px] leading-relaxed text-[color:var(--color-paper-dim)]">
      {JSON.stringify(input, null, 2)}
    </pre>
  );
}

export function FieldBlock({
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
export function InlineApprovalBar({
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

  // allow_session 仅 Bash / install_libs / file_verify 支持; 其它工具隐藏该按钮.
  const supportsAllowSession = ALLOW_SESSION_TOOLS.includes(toolName);

  // session 按钮是否可点 — 各工具按需要的字段判. 跟后端 allowlist_key 计算口径一致.
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
    if (toolName === INSTALL_BROWSER_USE_TOOL_NAME) {
      const step = String(input.step ?? "").trim();
      return step === "check" || step === "install" || step === "chromium";
    }
    if (toolName === BROWSER_USE_TOOL_NAME) {
      return !!String(input.command ?? "").trim();
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
          : toolName === INSTALL_BROWSER_USE_TOOL_NAME
            ? "Allow same step (session)"
            : toolName === BROWSER_USE_TOOL_NAME
              ? "Allow same action (session)"
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
      // ToolRow 自然回退到普通形态. busy 状态在那之前显示为 "Sending…".
    } catch (err) {
      toast.error(`Response failed: ${String(err)}`);
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-t border-[color:var(--color-line)] pt-2.5">
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
                  : toolName === BROWSER_USE_TOOL_NAME
                    ? "command 为空, 无法加入白名单"
                    : toolName === INSTALL_BROWSER_USE_TOOL_NAME
                      ? "step 不合法, 无法加入白名单"
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
