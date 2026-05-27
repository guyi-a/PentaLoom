// FolderPicker — 模态弹窗, 让用户在本机文件系统里挑一个目录挂载到 session.
//
// 行为:
//   - 打开时默认从 $HOME 列起 (后端 /fs/browse 不传 path 就是 $HOME)
//   - 顶部是面包屑式的当前路径 + 上一级 / 回家 按钮
//   - 中间是子目录列表 (点击进入)
//   - 底部 Cancel + Select this directory (把当前 path 回调出去)
//   - 已经被加过的路径 (alreadyAdded) 在列表里灰掉不可点

import { useEffect, useState } from "react";
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

export function FolderPicker({ onSelect, onCancel, alreadyAdded = [] }: Props) {
  const [data, setData] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  async function load(path?: string) {
    setLoading(true);
    setErr(null);
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

  function pickCurrent() {
    if (!data) return;
    if (alreadyAdded.includes(data.path)) {
      toast.error("Already added");
      return;
    }
    onSelect(data.path);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="flex h-[min(560px,80vh)] w-[min(560px,92vw)] flex-col overflow-hidden rounded-[8px] border border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-card)] shadow-[0_20px_60px_-15px_rgba(20,30,50,0.18)]">
        {/* 顶: 路径 + 上一级/home */}
        <div className="flex items-center gap-2 border-b border-[color:var(--color-line)] px-4 py-3">
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
            className="ml-1 flex-1 truncate font-mono text-[12px] text-[color:var(--color-paper)]"
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
            <div className="px-4 py-6 text-[12px] text-[color:var(--color-error)]">
              {err}
            </div>
          )}
          {!loading && !err && data && data.entries.length === 0 && (
            <div className="px-4 py-6 text-center text-[12px] text-[color:var(--color-ink)]">
              No subdirectories.
            </div>
          )}
          {!loading && !err && data && data.entries.length > 0 && (
            <ul>
              {data.entries.map((e) => {
                const taken = alreadyAdded.includes(e.path);
                return (
                  <li key={e.path}>
                    <button
                      type="button"
                      onClick={() => !taken && load(e.path)}
                      disabled={taken}
                      className={cn(
                        "flex w-full items-center gap-2.5 rounded-[5px] px-3 py-1.5 text-left text-[12px] transition-colors",
                        taken
                          ? "cursor-not-allowed text-[color:var(--color-ink-dim)]"
                          : "text-[color:var(--color-paper-dim)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]",
                      )}
                    >
                      <Folder
                        size={13}
                        className={cn(
                          taken
                            ? "text-[color:var(--color-ink-dim)]"
                            : "text-[color:var(--color-thread-file)]",
                        )}
                      />
                      <span className="truncate font-mono">{e.name}</span>
                      {taken && (
                        <span className="ml-auto text-[10px] text-[color:var(--color-ink-dim)]">
                          added
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {data?.truncated && (
            <div className="px-3 py-2 text-[10px] text-[color:var(--color-ink)]">
              · list truncated, dir has more entries
            </div>
          )}
        </div>

        {/* 底: 取消 / 选定 */}
        <div className="flex items-center gap-2 border-t border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-4 py-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:text-[color:var(--color-paper)]"
          >
            Cancel
          </button>
          <div className="flex-1" />
          <button
            type="button"
            onClick={pickCurrent}
            disabled={!data || loading || alreadyAdded.includes(data?.path ?? "")}
            className={cn(
              "rounded-[5px] px-3 py-1.5 text-[12px] font-medium transition-colors",
              data && !loading && !alreadyAdded.includes(data.path)
                ? "bg-[color:var(--color-accent)] text-white hover:opacity-90"
                : "cursor-not-allowed bg-[color:var(--color-bg-raised)] text-[color:var(--color-ink)]",
            )}
          >
            Select this folder
          </button>
        </div>
      </div>
    </div>
  );
}
