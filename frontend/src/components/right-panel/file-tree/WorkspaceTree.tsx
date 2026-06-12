// 右栏 Workspace 区主容器 — 取代旧 WorkspaceSection 的"路径 list"形态.
//
// 含 sandbox + 用户挂载的目录, 每个都是一棵 TreeRoot. 顶部 [+] 加挂载.
// 删挂载 UI 暂未做 (跟旧版一致), follow-up.

import { useState } from "react";
import { FolderPlus, Layers } from "lucide-react";
import { toast } from "sonner";

import { FolderPicker } from "@/components/permission/FolderPicker";
import { api } from "@/lib/api";
import { cn, shortenPath } from "@/lib/utils";

import { TreeRoot } from "./TreeRoot";

interface Props {
  sessionId: string;
  sandboxDir: string;
  mountedDirs: string[];
  onMountsChanged: () => void;
}

export function WorkspaceTree({
  sessionId,
  sandboxDir,
  mountedDirs,
  onMountsChanged,
}: Props) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busyAdd, setBusyAdd] = useState(false);

  async function addMount(path: string) {
    if (!sessionId) return;
    setBusyAdd(true);
    setPickerOpen(false);
    try {
      await api.patchMounts(sessionId, { add: [path] });
      toast.success(`Mounted ${shortenPath(path, 40)}`);
      onMountsChanged();
    } catch (err) {
      toast.error(`Mount failed: ${String(err)}`);
    } finally {
      setBusyAdd(false);
    }
  }

  // sandbox + mounts 总数, 跟旧版一致显示在右上 chip
  const totalCount = (sandboxDir ? 1 : 0) + mountedDirs.length;

  return (
    <section className="px-3 py-3">
      <div className="mb-2 flex items-center gap-1.5">
        <Layers size={11} className="shrink-0 text-[color:var(--color-ink-dim)]" />
        <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">
          Workspace
        </span>
        <span className="tabular ml-auto font-mono text-[10.5px] text-[color:var(--color-ink)]">
          {totalCount} dir{totalCount === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={() => setPickerOpen(true)}
          disabled={busyAdd || !sessionId}
          title={
            sessionId
              ? "Add mount"
              : "Workspace will be editable once the thread starts"
          }
          className={cn(
            "ml-1 rounded-[4px] p-1 text-[color:var(--color-ink)] transition-colors",
            busyAdd || !sessionId
              ? "cursor-not-allowed opacity-50"
              : "hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]",
          )}
        >
          <FolderPlus size={12} />
        </button>
      </div>

      <ul className="space-y-0.5">
        {sandboxDir && (
          <TreeRoot
            sessionId={sessionId}
            rootPath={sandboxDir}
            kind="sandbox"
          />
        )}
        {mountedDirs.length === 0 && !sandboxDir && (
          <li className="px-1 py-2 font-display text-[12px] italic text-[color:var(--color-ink)]">
            No directories mounted yet.
          </li>
        )}
        {mountedDirs.map((d) => (
          <TreeRoot
            key={d}
            sessionId={sessionId}
            rootPath={d}
            kind="mount"
          />
        ))}
      </ul>

      {pickerOpen && (
        <FolderPicker
          onSelect={addMount}
          onCancel={() => setPickerOpen(false)}
          alreadyAdded={mountedDirs}
        />
      )}
    </section>
  );
}
