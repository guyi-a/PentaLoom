// 三栏布局的右栏容器. 内部三个 section: Todo / Workspace / Context.
// 响应式由 ChatPage 决定 (panelOpen state + viewport class), 这里只负责内容.
//
import type { Frame, HistoryMessage, SessionMeta } from "@/lib/types";

import { ContextSection } from "./ContextSection";
import { TodoSection } from "./TodoSection";
import { WorkspaceSection } from "./WorkspaceSection";

interface Props {
  sessionId: string;
  meta: SessionMeta;
  history: HistoryMessage[];
  liveFrames: Frame[];
  onMountsChanged: () => void;
}

export function RightPanel({
  sessionId,
  meta,
  history,
  liveFrames,
  onMountsChanged,
}: Props) {
  return (
    <div className="flex h-full min-h-0 flex-col border-l border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)]">
      {/* 内容 — 整体可滚, 但 ContextSection 内部 ul 也 cap 自己的 max-h */}
      <div className="scrollbar-hidden min-h-0 flex-1 overflow-y-auto">
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
