// 三栏布局的右栏容器. 内部三个 section: Todo / Workspace / Context.
// 响应式由 ChatPage 决定 (panelOpen state + viewport class), 这里只负责内容.
//
// 关闭按钮: 头部右侧 X — ChatPage 把 setPanelOpen(false) 透下来.

import { X } from "lucide-react";

import type { Frame, HistoryMessage, SessionMeta } from "@/lib/types";

import { ContextSection } from "./ContextSection";
import { TodoSection } from "./TodoSection";
import { WorkspaceSection } from "./WorkspaceSection";

interface Props {
  sessionId: string;
  meta: SessionMeta;
  history: HistoryMessage[];
  liveFrames: Frame[];
  onClose: () => void;
  onMountsChanged: () => void;
}

export function RightPanel({
  sessionId,
  meta,
  history,
  liveFrames,
  onClose,
  onMountsChanged,
}: Props) {
  return (
    <div className="flex h-full min-h-0 flex-col border-l border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)]">
      {/* 头 */}
      <div className="flex items-center justify-between border-b border-[color:var(--color-line)] px-3 py-2">
        <span className="font-mono text-[11px] uppercase tracking-[0.15em] text-[color:var(--color-ink-dim)]">
          context
        </span>
        <button
          type="button"
          onClick={onClose}
          title="Close panel"
          className="rounded-[4px] p-1 text-[color:var(--color-ink)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
        >
          <X size={12} />
        </button>
      </div>

      {/* 内容 — 整体可滚, 但 ContextSection 内部 ul 也 cap 自己的 max-h */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <TodoSection history={history} liveFrames={liveFrames} />
        <WorkspaceSection
          sessionId={sessionId}
          mountedDirs={meta.mounted_dirs}
          onMountsChanged={onMountsChanged}
        />
        <ContextSection
          sessionId={sessionId}
          history={history}
          liveFrames={liveFrames}
        />
      </div>
    </div>
  );
}
