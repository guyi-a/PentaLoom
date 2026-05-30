// FolderPicker — 模态弹窗, 让用户在本机文件系统里挑一个目录挂载到 session.
//
// 行为 (macOS Finder 一致):
//   - 单击 = 选中 (高亮), 双击 = 进入子目录
//   - 没选 entry 时 Select 按钮选当前打开的目录
//   - 选了 entry 时 Select 按钮选这个 entry (文案 dynamic: "Select <basename>")
//   - 进入新目录 / 切换 path 时清掉选中
//   - 已挂载的目录 (alreadyAdded) 灰掉不可选不可进
//
// 顶部 path bar 用 mono (path 是技术结构), 列表 entry 名用 body sans (Finder 也是).

import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, Folder, Home, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { BrowseResponse } from "@/lib/types";
import { cn, shortenPath } from "@/lib/utils";

interface Props {
  onSelect: (absPath: string) => void;
  onCancel: () => void;
  alreadyAdded?: string[];
}

function basename(p: string): string {
  const parts = p.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? p;
}

export function FolderPicker({ onSelect, onCancel, alreadyAdded = [] }: Props) {
  const [data, setData] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selectedEntry, setSelectedEntry] = useState<string | null>(null);

  async function load(path?: string) {
    setLoading(true);
    setErr(null);
    setSelectedEntry(null); // 进入新目录清掉之前的选中
    try {
      const r = await api.browseDir(path);
      setData(r);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // 实际要选的路径 = 用户单击选中的 entry, 否则当前打开的目录
  const pickPath = selectedEntry ?? data?.path ?? null;
  const pickName = useMemo(
    () => (pickPath ? basename(pickPath) : null),
    [pickPath],
  );
  const canPick =
    !!pickPath && !loading && !alreadyAdded.includes(pickPath);

  function handlePick() {
    if (!pickPath) return;
    if (alreadyAdded.includes(pickPath)) {
      toast.error("Already added");
      return;
    }
    onSelect(pickPath);
  }

  // ESC 关闭
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
      if (e.key === "Enter" && canPick) handlePick();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canPick, pickPath]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="flex h-[min(580px,80vh)] w-[min(560px,92vw)] flex-col overflow-hidden rounded-[10px] border border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-card)] shadow-[0_20px_60px_-15px_rgba(20,30,50,0.18)]">
        {/* modal 标题 — Fraunces italic, 跟左栏 Threads / 右栏 Todo 同款 brand voice */}
        <div className="border-b border-[color:var(--color-line-soft)] px-5 pt-4 pb-3">
          <h2 className="font-display text-[16px] italic text-[color:var(--color-paper)]">
            Mount a folder
          </h2>
          <p className="mt-0.5 text-[11.5px] text-[color:var(--color-ink)]">
            Double-click to enter; single-click to select; press Enter to mount.
          </p>
        </div>

        {/* 顶: 路径 + 上一级/home */}
        <div className="flex items-center gap-1 border-b border-[color:var(--color-line-soft)] px-3 py-2">
          <button
            type="button"
            onClick={() => data?.parent && load(data.parent)}
            disabled={!data?.parent || loading}
            className={cn(
              "rounded-[5px] p-1.5 transition-colors",
              data?.parent && !loading
                ? "text-[color:var(--color-paper-dim)] hover:bg-[color:var(--color-bg-raised)]"
                : "cursor-not-allowed text-[color:var(--color-ink-dim)]",
            )}
            title="Up"
          >
            <ChevronLeft size={14} />
          </button>
          <button
            type="button"
            onClick={() => data && load(data.home)}
            disabled={loading}
            className="rounded-[5px] p-1.5 text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] disabled:cursor-not-allowed disabled:opacity-50"
            title="Home"
          >
            <Home size={14} />
          </button>
          <div
            className="ml-1 flex-1 truncate font-mono text-[12px] text-[color:var(--color-paper-dim)]"
            title={data?.path}
          >
            {data ? shortenPath(data.path, 56) : "…"}
          </div>
        </div>

        {/* 体: 列表 */}
        <div className="flex-1 overflow-y-auto px-1 py-2">
          {loading && (
            <div className="flex h-full items-center justify-center text-[color:var(--color-ink)]">
              <Loader2 size={16} className="animate-spin" />
            </div>
          )}
          {err && !loading && (
            <div className="px-4 py-6 font-display text-[12px] italic text-[color:var(--color-error)]">
              {err}
            </div>
          )}
          {!loading && !err && data && data.entries.length === 0 && (
            <div className="px-4 py-6 text-center font-display text-[12px] italic text-[color:var(--color-ink)]">
              No subdirectories.
            </div>
          )}
          {!loading && !err && data && data.entries.length > 0 && (
            <ul>
              {data.entries.map((e) => {
                const taken = alreadyAdded.includes(e.path);
                const active = selectedEntry === e.path;
                return (
                  <li key={e.path}>
                    <button
                      type="button"
                      onClick={() => !taken && setSelectedEntry(e.path)}
                      onDoubleClick={() => !taken && load(e.path)}
                      disabled={taken}
                      className={cn(
                        "flex w-full items-center gap-2.5 rounded-[6px] px-3 py-1.5 text-left text-[13px] transition-colors",
                        taken
                          ? "cursor-not-allowed text-[color:var(--color-ink-dim)]"
                          : active
                            ? "bg-[color:var(--color-bg-raised)] text-[color:var(--color-paper)]"
                            : "text-[color:var(--color-paper-dim)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]",
                      )}
                    >
                      <Folder
                        size={13}
                        className={cn(
                          "shrink-0",
                          taken
                            ? "text-[color:var(--color-ink-dim)]"
                            : "text-[color:var(--color-thread-file)]",
                        )}
                      />
                      <span className="truncate">{e.name}</span>
                      {taken && (
                        <span className="ml-auto font-display text-[10.5px] italic text-[color:var(--color-ink-dim)]">
                          mounted
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {data?.truncated && (
            <div className="px-3 py-2 text-[10.5px] italic text-[color:var(--color-ink)]">
              · list truncated, directory has more entries
            </div>
          )}
        </div>

        {/* 底: 取消 / 选定 (Cancel ghost, Select primary; Select 文案 dynamic 跟选中走) */}
        <div className="flex items-center gap-2 border-t border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-soft)] px-4 py-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-[6px] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
          >
            Cancel
          </button>
          <div className="flex-1" />
          <button
            type="button"
            onClick={handlePick}
            disabled={!canPick}
            className={cn(
              "rounded-[6px] px-3 py-1.5 text-[12px] font-medium transition-colors",
              canPick
                ? "bg-[color:var(--color-accent)] text-white hover:opacity-90"
                : "cursor-not-allowed bg-[color:var(--color-bg-raised)] text-[color:var(--color-ink)]",
            )}
            title={canPick && pickPath ? `Mount ${pickPath}` : undefined}
          >
            {pickName ? `Mount ${pickName}` : "Mount"}
          </button>
        </div>
      </div>
    </div>
  );
}
