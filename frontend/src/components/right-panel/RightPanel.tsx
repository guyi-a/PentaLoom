// 三栏布局的右栏容器. 内部两个 section: Todo / Workspace (含文件树).
//
// 旧 ContextSection (chip list 累积 tool_use 路径) 在 M19.0 重构时删了 —
// 跟实际文件系统脱节, rename/delete 后变 404; 价值被"WorkspaceTree 真实树形" +
// "ToolRow 文件名直接 click → preview"取代.

import type { Frame, HistoryMessage, SessionMeta } from "@/lib/types";

import { TodoSection } from "./TodoSection";
import { WorkspaceTree } from "./file-tree/WorkspaceTree";

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
      <div className="scrollbar-hidden min-h-0 flex-1 overflow-y-auto">
        <TodoSection history={history} liveFrames={liveFrames} />
        <WorkspaceTree
          sessionId={sessionId}
          sandboxDir={meta.sandbox_dir}
          mountedDirs={meta.mounted_dirs}
          onMountsChanged={onMountsChanged}
        />
      </div>
    </div>
  );
}
