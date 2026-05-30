// 通用 collapsible 分组组件 — sidebar 用. M11 只有一个 "Threads" 分组, M12 加
// app_gen 时复用此组件多加一个 "Apps" 分组, 不返工.
//
// 设计:
//   - label: Fraunces italic 11px ink-dim 小字, 杂志编辑部目录页风
//   - 默认展开, chevron 点击 toggle, 不持久化展开状态 (per-session 内存即可)
//   - children 用 slot, 不限制内部布局 (SessionList 内部还会再做时间细分组)

import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

interface Props {
  label: string;
  children: ReactNode;
  defaultExpanded?: boolean;
  className?: string;
  // 右侧 action 槽 — 给排序 / 新建之类按钮留位. M11 不用, M12 Apps 分组可能加 "New App"
  rightSlot?: ReactNode;
}

export function SidebarGroup({
  label,
  children,
  defaultExpanded = true,
  className,
  rightSlot,
}: Props) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className={cn("mb-1", className)}>
      <div className="group flex items-center gap-1 px-3 py-1">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex flex-1 items-center gap-1.5 text-left"
        >
          {expanded ? (
            <ChevronDown size={11} className="text-[color:var(--color-ink-dim)] transition-transform" />
          ) : (
            <ChevronRight size={11} className="text-[color:var(--color-ink-dim)] transition-transform" />
          )}
          <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">
            {label}
          </span>
        </button>
        {rightSlot && <div className="flex shrink-0 items-center gap-0.5">{rightSlot}</div>}
      </div>
      {expanded && <div className="mt-0.5">{children}</div>}
    </div>
  );
}
