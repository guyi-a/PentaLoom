// 单棵根 — sandbox 或一个 mount. 自己 SWR 拉 /fs/tree, 缓存, 提供 refresh.
//
// 设计:
//   - 默认折叠 (root expanded=false), 用户点击 root 一行才展开
//   - SWR key 含 sessionId + path, sid 切换自动失效; mutate 提供 refresh
//   - expanded Set 在自己内部管 (默认含 root path = 展开根; 子目录默认折叠由用户点)
//   - mount/sandbox root 行视觉区别于子目录: 加 source label (sandbox / mount), 加 reveal 按钮
//   - 顶部 refresh 按钮 (root 行右侧 hover 出, 跟 reveal 同款 hover-only 风格)

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  Box,
  ChevronRight,
  FolderOpen,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { usePreviewStore, type PreviewFile } from "@/lib/preview-store";
import type { FsTreeNode } from "@/lib/types";
import { cn } from "@/lib/utils";

import { FileTree } from "./FileTree";

interface Props {
  sessionId: string;
  /** 根路径 — sandbox 或一个 mounted_dir. */
  rootPath: string;
  /** sandbox vs mount — 决定 root 行的 icon 跟 label. */
  kind: "sandbox" | "mount";
}

export function TreeRoot({ sessionId, rootPath, kind }: Props) {
  // 默认 root 展开 (用户能立即看到顶层子目录), 子目录用户自己点开
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set([rootPath]),
  );
  const [rootOpen, setRootOpen] = useState(true);

  const previewPath = usePreviewStore((s) => s.previewFile?.path ?? null);
  const openPreview = usePreviewStore((s) => s.openPreview);

  const { data, error, isLoading, mutate, isValidating } = useSWR<FsTreeNode>(
    sessionId && rootOpen ? ["fs:tree", sessionId, rootPath] : null,
    () => api.getFsTree(sessionId, rootPath),
    { revalidateOnFocus: false },
  );

  const rootName = useMemo(() => {
    // sandbox 显示 "sandbox", mount 显示路径的 basename (回退到 path 末段)
    if (kind === "sandbox") return "sandbox";
    const parts = rootPath.split("/").filter(Boolean);
    return parts[parts.length - 1] ?? rootPath;
  }, [rootPath, kind]);

  function toggleNode(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function handleSelectFile(node: FsTreeNode) {
    const file: PreviewFile = {
      path: node.path,
      name: node.name,
      relativePath: rootPath && node.path.startsWith(rootPath)
        ? node.path.slice(rootPath.length).replace(/^\/+/, "")
        : undefined,
    };
    openPreview(file);
  }

  async function reveal() {
    try {
      await api.openFile({ sessionId, path: rootPath, reveal: true });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  return (
    <li className="group">
      {/* Root 行 — sandbox/mount 各自风格, hover 出 reveal/refresh 按钮 */}
      <div className="flex items-center gap-1 rounded-[3px] py-0.5 pl-1 pr-1 hover:bg-[color:var(--color-bg-raised)]">
        <button
          type="button"
          onClick={() => setRootOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-1 text-left"
          title={rootPath}
        >
          <ChevronRight
            size={11}
            className={cn(
              "shrink-0 text-[color:var(--color-ink-dim)] transition-transform",
              rootOpen && "rotate-90",
            )}
          />
          {kind === "sandbox" ? (
            <Box
              size={12}
              className="shrink-0 text-[color:var(--color-ink-dim)]"
            />
          ) : (
            <FolderOpen
              size={12}
              className="shrink-0 text-[color:var(--color-thread-file)]"
            />
          )}
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-[color:var(--color-paper)]">
            {rootName}
          </span>
          <span
            className="tabular shrink-0 font-mono text-[9.5px] text-[color:var(--color-ink-dim)]"
            title={rootPath}
          >
            {""}
          </span>
        </button>
        <button
          type="button"
          onClick={() => mutate()}
          disabled={isValidating}
          title="Refresh tree"
          className="shrink-0 rounded-[3px] p-0.5 text-[color:var(--color-ink)] opacity-0 transition-opacity hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)] group-hover:opacity-100 disabled:opacity-30"
        >
          <RefreshCw size={10} className={isValidating ? "animate-spin" : ""} />
        </button>
        <button
          type="button"
          onClick={reveal}
          title="Open in Finder"
          className="shrink-0 rounded-[3px] p-0.5 text-[color:var(--color-ink)] opacity-0 transition-opacity hover:bg-[color:var(--color-bg-card)] hover:text-[color:var(--color-paper)] group-hover:opacity-100"
        >
          <FolderOpen size={10} />
        </button>
      </div>

      {/* 树 — 用户展开 root 才拉; 子节点自己 lazy 渲染但数据 eager 拉 (一次 max_depth=8) */}
      {rootOpen && (
        <div>
          {isLoading && (
            <div
              className="flex items-center gap-1.5 py-1 pl-6 font-mono text-[10.5px] text-[color:var(--color-ink-dim)]"
            >
              <Loader2 size={10} className="animate-spin" />
              <span>Loading…</span>
            </div>
          )}
          {error && (
            <div className="py-1 pl-6 font-mono text-[10.5px] text-[color:var(--color-error)]">
              {error instanceof Error ? error.message : String(error)}
            </div>
          )}
          {data && data.children && (
            <FileTree
              nodes={data.children}
              expanded={expanded}
              onToggle={toggleNode}
              onSelectFile={handleSelectFile}
              selectedPath={previewPath}
              depth={1}
            />
          )}
          {data && (!data.children || data.children.length === 0) && !isLoading && (
            <div className="py-1 pl-6 font-display text-[11px] italic text-[color:var(--color-ink-dim)]">
              (empty)
            </div>
          )}
        </div>
      )}
    </li>
  );
}
