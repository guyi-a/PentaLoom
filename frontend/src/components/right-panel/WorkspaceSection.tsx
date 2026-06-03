// Workspace 区: sandbox (默认 cwd, 不可删) + meta.mounted_dirs 列表 + [+] 加挂载 +
// 每条 hover 给 [Open in Finder].
//
// sandbox 是 settings.sandbox_dir_for(sid) — agent 默认 cwd, weave 出来的产物默认
// 写这里 (然后再 symlink 到 data_dir/weaver/...). 用户应该看到, 但不能像 mount 那样
// 移除 — 它是 session 的隐含基线.
//
// 加挂载: 调 api.patchMounts({add:[path]}) → 后端 evict LoomPool entry → 下一条
// 消息触发 client 重建 (旧 ClaudeAgentOptions add_dirs 拿不到新挂载). 用户在本 turn
// 中途加挂载, 本 turn 内不生效 — 跟 request_workspace_dir 语义一致.
//
// 删挂载: 暂未提供 UI 入口 (需要先想清楚: turn 进行中能删? mounted=[] 时怎么办?
// LoomPool 重建后 cwd 走哪里). 留给后续.

import { useState } from "react";
import { Box, FolderOpen, FolderPlus, Layers } from "lucide-react";
import { toast } from "sonner";

import { FolderPicker } from "@/components/permission/FolderPicker";
import { api } from "@/lib/api";
import { cn, shortenPath } from "@/lib/utils";

interface Props {
  sessionId: string;
  sandboxDir: string;
  mountedDirs: string[];
  onMountsChanged: () => void; // ChatPage 触发 mutateMeta + sidebar 刷新
}

export function WorkspaceSection({
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

  async function revealInFinder(path: string) {
    if (!sessionId) return;
    try {
      await api.openFile({ sessionId, path, reveal: true });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  // sandbox + mounts 一起计数, 给用户"我这个 session 当前能看见几个目录"的整体感
  const totalCount = (sandboxDir ? 1 : 0) + mountedDirs.length;

  return (
    <section className="border-b border-[color:var(--color-line-soft)] px-3 py-3">
      <div className="mb-2 flex items-center gap-1.5">
        <Layers size={11} className="shrink-0 text-[color:var(--color-ink-dim)]" />
        <span className="font-display text-[12px] italic text-[color:var(--color-ink)]">Workspace</span>
        <span className="tabular ml-auto font-mono text-[10.5px] text-[color:var(--color-ink)]">
          {totalCount} dir{totalCount === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={() => setPickerOpen(true)}
          disabled={busyAdd || !sessionId}
          title={sessionId ? "Add mount" : "Workspace will be editable once the thread starts"}
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
          <li className="group">
            <div
              className="flex items-center gap-2 rounded-[4px] px-1.5 py-1 hover:bg-[color:var(--color-bg-raised)]"
              title={`Sandbox · ${sandboxDir}`}
            >
              <Box
                size={12}
                className="shrink-0 text-[color:var(--color-ink-dim)]"
              />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[11.5px] text-[color:var(--color-paper-dim)]">
                  sandbox
                </div>
                <div className="truncate font-mono text-[10px] text-[color:var(--color-ink)]">
                  {shortenPath(sandboxDir, 38)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => revealInFinder(sandboxDir)}
                title="Open sandbox in Finder/Explorer"
                disabled={!sessionId}
                className="shrink-0 rounded-[3px] p-0.5 text-[color:var(--color-ink)] opacity-0 transition-opacity hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)] group-hover:opacity-100"
              >
                <FolderOpen size={11} />
              </button>
            </div>
          </li>
        )}
        {mountedDirs.length === 0 && !sandboxDir && (
          <li className="px-1 py-2 font-display text-[12px] italic text-[color:var(--color-ink)]">
            No directories mounted yet.
          </li>
        )}
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
                disabled={!sessionId}
                className="shrink-0 rounded-[3px] p-0.5 text-[color:var(--color-ink)] opacity-0 transition-opacity hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)] group-hover:opacity-100"
              >
                <FolderOpen size={11} />
              </button>
            </div>
          </li>
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
