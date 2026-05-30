// Workspace 区: meta.mounted_dirs 列表 + [+] 加挂载 + 每条 hover 给 [Open in Finder].
//
// 加挂载: 调 api.patchMounts({add:[path]}) → 后端 evict LoomPool entry → 下一条
// 消息触发 client 重建 (旧 ClaudeAgentOptions add_dirs 拿不到新挂载). 用户在本 turn
// 中途加挂载, 本 turn 内不生效 — 跟 request_workspace_dir 语义一致.
//
// 删挂载: 暂未提供 UI 入口 (需要先想清楚: turn 进行中能删? mounted=[] 时怎么办?
// LoomPool 重建后 cwd 走哪里). 留给 M5+.

import { useState } from "react";
import { FolderOpen, FolderPlus, Layers } from "lucide-react";
import { toast } from "sonner";

import { FolderPicker } from "@/components/permission/FolderPicker";
import { api } from "@/lib/api";
import { cn, shortenPath } from "@/lib/utils";

interface Props {
  sessionId: string;
  mountedDirs: string[];
  onMountsChanged: () => void; // ChatPage 触发 mutateMeta + sidebar 刷新
}

export function WorkspaceSection({ sessionId, mountedDirs, onMountsChanged }: Props) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busyAdd, setBusyAdd] = useState(false);

  async function addMount(path: string) {
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

  async function revealInFinder(path: string) {
    try {
      await api.openFile({ sessionId, path, reveal: true });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  return (
    <section className="border-b border-[color:var(--color-line-soft)] px-3 py-3">
      <div className="mb-2 flex items-center gap-1.5">
        <Layers size={11} className="shrink-0 text-[color:var(--color-ink-dim)]" />
        <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">Workspace</span>
        <span className="tabular ml-auto font-mono text-[10.5px] text-[color:var(--color-ink)]">
          {mountedDirs.length} mount{mountedDirs.length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={() => setPickerOpen(true)}
          disabled={busyAdd}
          title="Add mount"
          className={cn(
            "ml-1 rounded-[4px] p-1 text-[color:var(--color-ink)] transition-colors",
            busyAdd
              ? "cursor-not-allowed opacity-50"
              : "hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]",
          )}
        >
          <FolderPlus size={12} />
        </button>
      </div>

      {mountedDirs.length === 0 ? (
        <div className="px-1 py-2 font-display text-[12px] italic text-[color:var(--color-ink)]">
          No directories mounted yet.
        </div>
      ) : (
        <ul className="space-y-0.5">
          {mountedDirs.map((d) => (
            <li key={d} className="group">
              <div className="flex items-center gap-2 rounded-[4px] px-1.5 py-1 hover:bg-[color:var(--color-bg-raised)]">
                <FolderOpen
                  size={12}
                  className="shrink-0 text-[color:var(--color-thread-file)]"
                />
                <span
                  className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)]"
                  title={d}
                >
                  {shortenPath(d, 38)}
                </span>
                <button
                  type="button"
                  onClick={() => revealInFinder(d)}
                  title="Open in Finder/Explorer"
                  className="shrink-0 rounded-[3px] p-0.5 text-[color:var(--color-ink)] opacity-0 transition-opacity hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)] group-hover:opacity-100"
                >
                  <FolderOpen size={11} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

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
