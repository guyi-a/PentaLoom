// WorkflowDetailModal — 点 sidebar Workflows 行弹出, 看 weaver workflow 详情.
//
// 跟 AppDetailModal 同风格, 但简化 (workflow 没 files / services / schedules / watches):
//   - Header: status badge + name + description + step_count + use_count + 刷新 + 关闭
//   - Inputs schema (扁平 key: type 列表)
//   - Steps (按顺序; click 展开看 invoke_app.args / call_llm.prompt / set_var.value)
//   - Recent runs (click 展开看每步 status / output_summary; failed step 红色)
//   - Meta footer

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import useSWR from "swr";
import {
  Brain,
  ChevronDown,
  ChevronRight,
  GitBranch,
  Loader2,
  Network,
  Package,
  RefreshCw,
  ShieldCheck,
  Workflow as WorkflowIcon,
  X,
} from "lucide-react";

import { api } from "@/lib/api";
import type {
  AppStatus,
  WorkflowDetailResponse,
  WorkflowRunLog,
  WorkflowStep,
  WorkflowStepLog,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  workflowName: string;
  onClose: () => void;
}

const statusBadge: Record<AppStatus, { label: string; cls: string }> = {
  draft:  { label: "draft",  cls: "text-[color:var(--color-ink-dim)] bg-[color:var(--color-bg-raised)]" },
  ready:  { label: "ready",  cls: "text-[#2d5a3d] bg-[#d4ead9]" },
  dirty:  { label: "dirty",  cls: "text-[#6b5400] bg-[#f4e4a0]" },
  failed: { label: "failed", cls: "text-[#7a2d2d] bg-[#f0c8c8]" },
};

function fmtMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function WorkflowDetailModal({ workflowName, onClose }: Props) {
  const { data, error, isLoading, mutate, isValidating } =
    useSWR<WorkflowDetailResponse>(
      workflowName ? `workflow-detail:${workflowName}` : null,
      () => api.getWorkflowDetail(workflowName),
      { revalidateOnFocus: false },
    );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const meta = data?.summary.meta ?? null;
  const definition = data?.summary.definition;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex h-[min(680px,85vh)] w-[min(720px,92vw)] flex-col overflow-hidden rounded-[10px] border border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-card)] shadow-[0_20px_60px_-15px_rgba(20,30,50,0.18)]">
        {/* Header */}
        <div className="flex items-start gap-3 border-b border-[color:var(--color-line-soft)] px-5 pt-4 pb-3">
          <GitBranch size={18} className="mt-0.5 shrink-0 text-[color:var(--color-thread-file)]" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="font-display text-[16px] italic text-[color:var(--color-paper)]">
                {workflowName}
              </h2>
              {meta && (
                <span
                  className={cn(
                    "tabular shrink-0 rounded-[3px] px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-wider",
                    statusBadge[meta.status].cls,
                  )}
                >
                  {statusBadge[meta.status].label}
                </span>
              )}
              {data && (
                <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
                  {data.summary.step_count} step{data.summary.step_count === 1 ? "" : "s"}
                </span>
              )}
              {meta && meta.use_count > 0 && (
                <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
                  · {meta.use_count} run{meta.use_count === 1 ? "" : "s"}
                </span>
              )}
            </div>
            {data && (
              <p className="mt-0.5 truncate text-[11.5px] text-[color:var(--color-ink)]">
                {data.summary.description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => mutate()}
            disabled={isValidating}
            title="Refresh"
            className="shrink-0 rounded-[5px] p-1 text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <RefreshCw size={13} className={isValidating ? "animate-spin" : ""} />
          </button>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 rounded-[5px] p-1 text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
            title="Close (Esc)"
          >
            <X size={14} />
          </button>
        </div>

        {/* 主体 — 滚动 */}
        <div className="scrollbar-hidden min-h-0 flex-1 overflow-y-auto px-5 py-3">
          {isLoading && (
            <div className="flex items-center gap-2 px-1 py-8 text-[color:var(--color-ink)]">
              <Loader2 size={14} className="animate-spin" />
              <span className="text-[12px] italic">Loading detail…</span>
            </div>
          )}
          {error && (
            <div className="rounded-[6px] border border-[#f0c8c8] bg-[#f8e8e8] px-3 py-2 text-[12px] text-[#7a2d2d]">
              加载失败: {String(error)}
            </div>
          )}

          {data && definition && (
            <div className="space-y-5">
              {meta?.status === "failed" && meta.last_finalize_error && (
                <Section title="Finalize error">
                  <pre className="whitespace-pre-wrap break-words rounded-[4px] bg-[#f8e8e8] px-2 py-1.5 font-mono text-[11px] text-[#7a2d2d]">
                    {meta.last_finalize_error}
                  </pre>
                </Section>
              )}

              {/* Inputs schema */}
              <Section
                title="Inputs"
                count={Object.keys(
                  (definition.inputs_schema?.properties as Record<string, unknown>) ?? {},
                ).length}
              >
                <InputsSchemaView schema={definition.inputs_schema} />
              </Section>

              {/* Steps */}
              <Section title="Steps" count={definition.steps.length}>
                {definition.steps.length === 0 ? (
                  <Placeholder>No steps.</Placeholder>
                ) : (
                  <ul className="space-y-1">
                    {definition.steps.map((step, i) => (
                      <StepRow key={step.id} idx={i} step={step} />
                    ))}
                  </ul>
                )}
              </Section>

              {/* Recent runs */}
              <Section title="Recent runs" count={data.recent_runs.length}>
                {data.recent_runs.length === 0 ? (
                  <Placeholder>No runs yet.</Placeholder>
                ) : (
                  <ul className="space-y-1">
                    {data.recent_runs
                      .slice()
                      .reverse()
                      .map((r, i) => (
                        <RunRow key={`${r.run_id}-${i}`} run={r} />
                      ))}
                  </ul>
                )}
              </Section>

              {/* Meta footer */}
              {meta && (
                <Section title="Meta">
                  <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
                    <MetaField label="version" value={definition.version} />
                    <MetaField label="source" value={meta.source} />
                    <MetaField label="created" value={fmtTime(meta.created_at)} />
                    <MetaField label="updated" value={fmtTime(meta.updated_at)} />
                    <MetaField
                      label="last finalized"
                      value={meta.last_finalized_at ? fmtTime(meta.last_finalized_at) : "—"}
                    />
                    <MetaField
                      label="last used"
                      value={meta.last_used_at ? fmtTime(meta.last_used_at) : "—"}
                    />
                    <MetaField label="use count" value={String(meta.use_count)} />
                  </dl>
                </Section>
              )}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ─── 内部小组件 ─────────────────────────────────────────────

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">
          {title}
        </span>
        {count !== undefined && (
          <span className="tabular font-mono text-[10px] text-[color:var(--color-ink-dim)]">
            · {count}
          </span>
        )}
      </div>
      {children}
    </section>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-1 py-1.5 font-display text-[11.5px] italic text-[color:var(--color-ink)]">
      {children}
    </div>
  );
}

function InputsSchemaView({ schema }: { schema: Record<string, unknown> }) {
  const props = (schema?.properties as Record<string, Record<string, unknown>>) ?? {};
  const required = (schema?.required as string[]) ?? [];
  const keys = Object.keys(props);
  if (keys.length === 0) {
    return <Placeholder>No inputs declared.</Placeholder>;
  }
  return (
    <ul className="space-y-0.5">
      {keys.map((k) => {
        const t = String(props[k]?.type ?? "any");
        const isRequired = required.includes(k);
        const desc =
          typeof props[k]?.description === "string"
            ? (props[k].description as string)
            : "";
        return (
          <li
            key={k}
            className="flex items-center gap-2 rounded-[4px] px-1.5 py-1"
            title={desc}
          >
            <span className="font-mono text-[11.5px] text-[color:var(--color-paper)]">
              {k}
            </span>
            <span className="tabular font-mono text-[10px] text-[color:var(--color-ink-dim)]">
              {t}
            </span>
            {isRequired && (
              <span className="tabular shrink-0 rounded-[3px] bg-[color:var(--color-bg-raised)] px-1 py-px font-mono text-[9px] uppercase tracking-wider text-[color:var(--color-paper-dim)]">
                required
              </span>
            )}
            {desc && (
              <span className="min-w-0 flex-1 truncate text-[10.5px] text-[color:var(--color-ink-dim)]">
                {desc}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function StepRow({ idx, step }: { idx: number; step: WorkflowStep }) {
  const [open, setOpen] = useState(false);

  const kindMeta = stepKindMeta(step.kind);
  const summary = stepSummary(step);

  return (
    <li className="rounded-[4px] border border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-soft)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]"
      >
        {open ? (
          <ChevronDown size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        ) : (
          <ChevronRight size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        )}
        <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          #{idx + 1}
        </span>
        <kindMeta.Icon
          size={11}
          className={cn("shrink-0", kindMeta.cls)}
        />
        <span className="shrink-0 font-mono text-[11px] text-[color:var(--color-paper)]">
          {step.id}
        </span>
        <span className="tabular shrink-0 rounded-[3px] bg-[color:var(--color-bg-raised)] px-1 py-px font-mono text-[9px] uppercase tracking-wider text-[color:var(--color-paper-dim)]">
          {step.kind}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[color:var(--color-ink-dim)]">
          {summary}
        </span>
      </button>
      {open && (
        <div className="border-t border-[color:var(--color-line-soft)] px-2 py-1.5">
          <StepDetail step={step} />
        </div>
      )}
    </li>
  );
}

function StepDetail({ step }: { step: WorkflowStep }) {
  if (step.kind === "invoke_app") {
    return (
      <div className="space-y-1.5">
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px]">
          <MetaField label="app" value={step.app_name} />
          <MetaField label="invocation" value={step.invocation_id} />
        </dl>
        <Field label="args">
          <pre className="max-h-[200px] overflow-y-auto whitespace-pre-wrap break-words rounded-[3px] bg-[color:var(--color-bg-deep)] px-1.5 py-1 font-mono text-[11px] leading-relaxed text-[color:var(--color-paper)]">
            {JSON.stringify(step.args, null, 2)}
          </pre>
        </Field>
      </div>
    );
  }
  if (step.kind === "call_llm") {
    return (
      <div className="space-y-1.5">
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px]">
          <MetaField label="model" value={step.model ?? "(default)"} />
          <MetaField label="output_format" value={step.output_format} />
          <MetaField label="max_tokens" value={String(step.max_tokens)} />
          <MetaField label="timeout" value={`${step.timeout_s}s`} />
        </dl>
        {step.system && (
          <Field label="system">
            <pre className="max-h-[120px] overflow-y-auto whitespace-pre-wrap break-words rounded-[3px] bg-[color:var(--color-bg-deep)] px-1.5 py-1 font-mono text-[11px] leading-relaxed text-[color:var(--color-paper)]">
              {step.system}
            </pre>
          </Field>
        )}
        <Field label="prompt">
          <pre className="max-h-[200px] overflow-y-auto whitespace-pre-wrap break-words rounded-[3px] bg-[color:var(--color-bg-deep)] px-1.5 py-1 font-mono text-[11px] leading-relaxed text-[color:var(--color-paper)]">
            {step.prompt}
          </pre>
        </Field>
      </div>
    );
  }
  // set_var
  return (
    <Field label="value">
      <pre className="max-h-[200px] overflow-y-auto whitespace-pre-wrap break-words rounded-[3px] bg-[color:var(--color-bg-deep)] px-1.5 py-1 font-mono text-[11px] leading-relaxed text-[color:var(--color-paper)]">
        {JSON.stringify(step.value, null, 2)}
      </pre>
    </Field>
  );
}

function RunRow({ run }: { run: WorkflowRunLog }) {
  const [open, setOpen] = useState(false);
  const statusCls =
    run.status === "success" ? "text-[#2d5a3d]" : "text-[#7a2d2d]";
  const failedStepId = run.steps?.find((s) => s.status === "failed")?.id;
  return (
    <li className="rounded-[4px] border border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-soft)]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={run.error || run.run_id}
        className="flex w-full items-center gap-2 px-1.5 py-1 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]"
      >
        {open ? (
          <ChevronDown size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        ) : (
          <ChevronRight size={11} className="shrink-0 text-[color:var(--color-ink)]" />
        )}
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[color:var(--color-paper-dim)]">
          {run.run_id}
        </span>
        {failedStepId && (
          <span
            title={`failed at step ${failedStepId}`}
            className="tabular shrink-0 font-mono text-[10px] text-[#7a2d2d]"
          >
            ✗ {failedStepId}
          </span>
        )}
        <span
          className={cn(
            "tabular shrink-0 font-mono text-[10px] uppercase tracking-wider",
            statusCls,
          )}
        >
          {run.status}
        </span>
        <span className="tabular shrink-0 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
          {fmtMs(run.duration_ms)}
        </span>
      </button>
      {open && (
        <div className="border-t border-[color:var(--color-line-soft)] px-1.5 py-1.5">
          <div className="mb-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-[color:var(--color-ink-dim)]">
            <span>started: {fmtTime(run.started_at)}</span>
            {run.trigger && <span>trigger: {run.trigger}</span>}
          </div>
          {run.error && (
            <pre className="mb-1 whitespace-pre-wrap break-words rounded-[3px] bg-[#f8e8e8] px-1.5 py-1 font-mono text-[11px] text-[#7a2d2d]">
              {run.error}
            </pre>
          )}
          <ul className="space-y-0.5">
            {run.steps.map((s, i) => (
              <RunStepRow key={`${s.id}-${i}`} step={s} />
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function RunStepRow({ step }: { step: WorkflowStepLog }) {
  const statusCls =
    step.status === "success" ? "text-[#2d5a3d]" : "text-[#7a2d2d]";
  const kindMeta = stepKindMeta(step.kind);
  return (
    <li className="space-y-0.5 rounded-[3px] bg-[color:var(--color-bg-deep)] px-1.5 py-1">
      <div className="flex items-center gap-2">
        <kindMeta.Icon size={10} className={cn("shrink-0", kindMeta.cls)} />
        <span className="font-mono text-[11px] text-[color:var(--color-paper)]">
          {step.id}
        </span>
        <span className="tabular font-mono text-[9.5px] uppercase tracking-wider text-[color:var(--color-ink-dim)]">
          {step.kind}
        </span>
        {step.app_name && (
          <span className="tabular font-mono text-[9.5px] text-[color:var(--color-ink-dim)]">
            → {step.app_name}/{step.invocation_id}
          </span>
        )}
        <span className="flex-1" />
        <span
          className={cn(
            "tabular shrink-0 font-mono text-[9.5px] uppercase tracking-wider",
            statusCls,
          )}
        >
          {step.status}
        </span>
        <span className="tabular shrink-0 font-mono text-[9.5px] text-[color:var(--color-ink-dim)]">
          {fmtMs(step.duration_ms)}
        </span>
      </div>
      {step.error && (
        <pre className="whitespace-pre-wrap break-words rounded-[2px] bg-[#f8e8e8] px-1.5 py-0.5 font-mono text-[10.5px] text-[#7a2d2d]">
          {step.error}
        </pre>
      )}
      {step.output_summary !== undefined && step.output_summary !== null && (
        <pre className="max-h-[140px] overflow-y-auto whitespace-pre-wrap break-words font-mono text-[10.5px] text-[color:var(--color-paper-dim)]">
          {typeof step.output_summary === "string"
            ? step.output_summary
            : JSON.stringify(step.output_summary, null, 2)}
        </pre>
      )}
    </li>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-0.5 font-mono text-[9.5px] uppercase tracking-wider text-[color:var(--color-ink-dim)]">
        {label}
      </div>
      {children}
    </div>
  );
}

function MetaField({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="font-mono text-[color:var(--color-ink-dim)]">{label}</dt>
      <dd className="font-mono text-[color:var(--color-paper-dim)]">{value}</dd>
    </>
  );
}

function stepSummary(step: WorkflowStep): string {
  if (step.kind === "invoke_app") return `${step.app_name} · ${step.invocation_id}`;
  if (step.kind === "call_llm") {
    const head = step.prompt.split("\n")[0]?.slice(0, 60) ?? "";
    return head ? `${head}${step.prompt.length > 60 ? "…" : ""}` : "(empty prompt)";
  }
  // set_var
  const keys = Object.keys(step.value);
  return keys.length === 0 ? "(empty)" : `{${keys.slice(0, 4).join(", ")}${keys.length > 4 ? ", …" : ""}}`;
}

// kind 含 step kind + runtime 加的 pseudo "output_template" (output_template 渲染失败时插).
type StepKindLike = WorkflowStep["kind"] | "output_template";

function stepKindMeta(kind: StepKindLike) {
  if (kind === "invoke_app") {
    return { Icon: Package, cls: "text-[color:var(--color-thread-file)]" } as const;
  }
  if (kind === "call_llm") {
    return { Icon: Brain, cls: "text-[color:var(--color-thread-search)]" } as const;
  }
  if (kind === "output_template") {
    // pseudo step — 渲染 output_template 挂时插, 用 ShieldCheck 但飘红
    return { Icon: ShieldCheck, cls: "text-[#7a2d2d]" } as const;
  }
  // set_var
  return { Icon: Network, cls: "text-[color:var(--color-thread-computer)]" } as const;
}

// 顶部 GitBranch 用作 modal header 时, 跟 WeaverGroup row 的 icon 一致.
// WorkflowIcon 留作其它入口的备用 (例如未来 settings 页面).
export { WorkflowIcon };
