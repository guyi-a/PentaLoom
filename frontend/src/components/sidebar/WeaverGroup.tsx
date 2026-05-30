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
  BookOpen,
  Boxes,
  ChevronDown,
  ChevronRight,
  Sparkles,
  Workflow,
} from "lucide-react";

import { SidebarGroup } from "@/components/sidebar/SidebarGroup";
import { api } from "@/lib/api";
import type { SkillSummary, WeaverSource } from "@/lib/types";

const sourceLabel: Record<WeaverSource, string> = {
  builtin: "builtin",
  agent_woven: "woven",
  user_imported: "imported",
  user_handwritten: "handwritten",
};

export function WeaverGroup() {
  const { data, isLoading } = useSWR("weaver/products", () =>
    api.listWeaverProducts(),
  );
  const skills = data?.skills ?? [];

  return (
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

        <SubSection label="Subagents">
          <PlaceholderText icon={<Boxes size={10} />}>
            Coming with M17.
          </PlaceholderText>
        </SubSection>

        <SubSection label="Workflows">
          <PlaceholderText icon={<Workflow size={10} />}>
            Coming with M16.
          </PlaceholderText>
        </SubSection>

        <SubSection label="Apps">
          <PlaceholderText icon={<Sparkles size={10} />}>
            Coming with M18.
          </PlaceholderText>
        </SubSection>
      </div>
    </SidebarGroup>
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
