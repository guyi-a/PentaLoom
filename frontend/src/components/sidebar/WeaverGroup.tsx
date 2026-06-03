// Sidebar Weaver 分组 — 取代旧 ProjectsPlaceholder.
// M14: Skills 真实数据 (内置 + 用户织的); Subagents / Workflows / Apps 占位 (M17/M16/M18).
//
// 视觉层级:
//   - 外层 SidebarGroup "Weaver" — 跟 Threads 同款 12px italic, 默认展开
//   - 内层 SubSection (4 类产物) — 11px italic ink-dim + 缩进 + 小一号 chevron,
//     视觉上明确"次级", 跟 Threads 内部"时间子分组"同样的标题语言
//   - 所有子组默认折叠, 用户想看再点开 (sidebar 是导航, 不是展示墙)

import { useState, type ReactNode } from "react";
import useSWR from "swr";
import {
  AppWindow,
  BookOpen,
  Boxes,
  ChevronDown,
  ChevronRight,
  Workflow,
} from "lucide-react";

import { AppDetailModal } from "@/components/sidebar/AppDetailModal";
import { SidebarGroup } from "@/components/sidebar/SidebarGroup";
import { api } from "@/lib/api";
import type { AppStatus, AppSummary, SkillSummary, WeaverSource } from "@/lib/types";

interface Props {
  // 当前活跃 session id — 给 AppDetailModal 内 openFile 用. sidebar 自身跨 session,
  // 但点开 detail 看源码时需要 sid 走 fs 工具. 没活跃 session 时点击 modal 仍能开,
  // 但 openFile 会 toast 提示先开 thread.
  currentSid?: string;
}

const sourceLabel: Record<WeaverSource, string> = {
  builtin: "builtin",
  agent_woven: "woven",
  user_imported: "imported",
  user_handwritten: "handwritten",
};

// status badge 视觉: 状态机 4 档对应 4 种颜色 + 文本
// draft  = 灰 (写中)
// ready  = 绿 (能 invoke)
// dirty  = 黄 (改过没重 finalize)
// failed = 红 (上次 finalize 校验挂)
const statusBadge: Record<AppStatus, { label: string; cls: string }> = {
  draft:  { label: "draft",  cls: "text-[color:var(--color-ink-dim)] bg-[color:var(--color-bg-raised)]" },
  ready:  { label: "ready",  cls: "text-[#2d5a3d] bg-[#d4ead9]" },
  dirty:  { label: "dirty",  cls: "text-[#6b5400] bg-[#f4e4a0]" },
  failed: { label: "failed", cls: "text-[#7a2d2d] bg-[#f0c8c8]" },
};

export function WeaverGroup({ currentSid }: Props) {
  const { data, isLoading } = useSWR("weaver/products", () =>
    api.listWeaverProducts(),
  );
  const skills = data?.skills ?? [];
  const apps = data?.apps ?? [];
  const [openAppName, setOpenAppName] = useState<string | null>(null);

  return (
    <>
    <SidebarGroup label="Weaver" defaultExpanded={true}>
      <div className="space-y-0">
        <SubSection label="Skills" count={skills.length}>
          {isLoading ? (
            <PlaceholderText>Loading…</PlaceholderText>
          ) : skills.length === 0 ? (
            <PlaceholderText>No skills yet, weave one in chat.</PlaceholderText>
          ) : (
            <ul className="space-y-0.5">
              {skills.map((s) => (
                <SkillRow key={s.name} skill={s} />
              ))}
            </ul>
          )}
        </SubSection>

        <SubSection label="Apps" count={apps.length}>
          {isLoading ? (
            <PlaceholderText>Loading…</PlaceholderText>
          ) : apps.length === 0 ? (
            <PlaceholderText icon={<AppWindow size={10} />}>
              No apps yet, weave one in chat.
            </PlaceholderText>
          ) : (
            <ul className="space-y-0.5">
              {apps.map((a) => (
                <AppRow
                  key={a.name}
                  app={a}
                  onOpen={() => setOpenAppName(a.name)}
                />
              ))}
            </ul>
          )}
        </SubSection>

        <SubSection label="Subagents">
          <PlaceholderText icon={<Boxes size={10} />}>
            Coming with M17.
          </PlaceholderText>
        </SubSection>

        <SubSection label="Workflows">
          <PlaceholderText icon={<Workflow size={10} />}>
            Coming with workflow milestone.
          </PlaceholderText>
        </SubSection>
      </div>
    </SidebarGroup>
    {openAppName && (
      <AppDetailModal
        appName={openAppName}
        sessionId={currentSid ?? ""}
        onClose={() => setOpenAppName(null)}
      />
    )}
    </>
  );
}

// 次级分组 — 跟 SessionList 里"时间子分组"视觉语言对齐 (11px italic ink-dim).
// 缩进 ml-4 让用户一眼看出它在 Weaver 之下, 不是平级.
function SubSection({
  label,
  count,
  children,
}: {
  label: string;
  count?: number;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="ml-4">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-1 px-3 py-0.5 text-left"
      >
        {expanded ? (
          <ChevronDown size={9} className="text-[color:var(--color-ink)]" />
        ) : (
          <ChevronRight size={9} className="text-[color:var(--color-ink)]" />
        )}
        <span className="font-display text-[11px] italic text-[color:var(--color-ink)]">
          {label}
        </span>
        {count !== undefined && count > 0 && (
          <span className="tabular ml-1 font-mono text-[9.5px] text-[color:var(--color-ink-dim)]">
            · {count}
          </span>
        )}
      </button>
      {expanded && <div className="mt-0.5 ml-4">{children}</div>}
    </div>
  );
}

function SkillRow({ skill }: { skill: SkillSummary }) {
  return (
    <li
      title={`${skill.name} — ${skill.description} (${sourceLabel[skill.source]})`}
      className="group flex items-center gap-2 rounded-[6px] px-3 py-1.5 transition-colors hover:bg-[color:var(--color-bg-raised)]"
    >
      <BookOpen
        size={11}
        className="shrink-0 text-[color:var(--color-thread-file)] opacity-60"
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12.5px] leading-snug text-[color:var(--color-paper)]">
          {skill.name}
        </div>
        <div className="truncate text-[10.5px] leading-snug text-[color:var(--color-ink-dim)]">
          {skill.description}
        </div>
      </div>
      <span
        className={`tabular shrink-0 font-mono text-[9.5px] uppercase tracking-wider opacity-0 transition-opacity group-hover:opacity-100 ${
          skill.source === "builtin"
            ? "text-[color:var(--color-ink)]"
            : "text-[color:var(--color-thread-file)]"
        }`}
      >
        {sourceLabel[skill.source]}
      </span>
    </li>
  );
}

function AppRow({
  app,
  onOpen,
}: {
  app: AppSummary;
  onOpen: () => void;
}) {
  // component_counts 折成"3 scripts · 1 window" 这种 chip 文本, 0 的省略
  const compParts = Object.entries(app.component_counts)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${n === 1 ? k.replace(/s$/, "") : k}`);
  const tooltip = [
    `${app.name} — ${app.description}`,
    `status: ${app.status}`,
    `${app.invocation_count} invocation${app.invocation_count === 1 ? "" : "s"}`,
    compParts.length > 0 ? compParts.join(", ") : "no runtime components",
    app.has_app_definition ? "" : "manifest only (no app.json)",
    "Click to open detail",
  ].filter(Boolean).join(" · ");

  // hover 时右侧显示 "N inv" chip — 跟 SkillRow 的 source chip 同款.
  const invLabel = `${app.invocation_count} inv${app.invocation_count === 1 ? "" : "s"}`;
  const badge = statusBadge[app.status] ?? statusBadge.draft;

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        title={tooltip}
        className="group flex w-full items-center gap-2 rounded-[6px] px-3 py-1.5 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]"
      >
        <AppWindow
          size={11}
          className="shrink-0 text-[color:var(--color-thread-file)] opacity-60"
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12.5px] leading-snug text-[color:var(--color-paper)]">
            {app.name}
          </div>
          <div className="truncate text-[10.5px] leading-snug text-[color:var(--color-ink-dim)]">
            {app.description}
          </div>
        </div>
        {/* status badge 始终显示, 状态机一目了然 */}
        <span
          className={`tabular shrink-0 rounded-[3px] px-1 py-px font-mono text-[9px] uppercase tracking-wider ${badge.cls}`}
        >
          {badge.label}
        </span>
        {/* invocations chip — hover 显示 */}
        <span className="tabular shrink-0 font-mono text-[9.5px] uppercase tracking-wider text-[color:var(--color-thread-file)] opacity-0 transition-opacity group-hover:opacity-100">
          {invLabel}
        </span>
      </button>
    </li>
  );
}

function PlaceholderText({
  children,
  icon,
}: {
  children: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 text-[11px] leading-relaxed text-[color:var(--color-ink)]">
      {icon && <span className="shrink-0 opacity-60">{icon}</span>}
      <span className="font-display italic">{children}</span>
    </div>
  );
}
