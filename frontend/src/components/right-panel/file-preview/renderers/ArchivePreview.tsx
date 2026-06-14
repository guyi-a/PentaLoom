// zip 文件预览 — 后端 zipfile.ZipFile.infolist() 出 entries (只读 metadata 不解压),
// 前端 buildTree 构折叠目录树, TreeItem 递归渲. 第一层目录默认展开.
//
// 不引 file-icons 库 — lucide Folder/FolderOpen/File 三个图标兜底足够.
// tar/gz/7z 后续 PR 加, 第一版只支持 .zip.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronRight, ExternalLink, File, Folder, FolderOpen, Loader2, Package } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { ArchivePreview as ArchivePreviewData, ZipEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  sessionId: string;
  path: string;
}

interface TreeNode {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  compressed_size: number;
  children: TreeNode[];
}

function buildTree(entries: ZipEntry[]): TreeNode[] {
  const root: TreeNode[] = [];
  const dirMap = new Map<string, TreeNode>();

  // 目录递归 get-or-create. parts.length === 1 表示顶层, 直接挂 root.
  const getOrCreateDir = (dirPath: string): TreeNode => {
    const existing = dirMap.get(dirPath);
    if (existing) return existing;

    const parts = dirPath.replace(/\/$/, "").split("/");
    const name = parts[parts.length - 1];
    const node: TreeNode = {
      name,
      path: dirPath,
      is_dir: true,
      size: 0,
      compressed_size: 0,
      children: [],
    };
    dirMap.set(dirPath, node);

    if (parts.length === 1) {
      root.push(node);
    } else {
      const parentPath = parts.slice(0, -1).join("/") + "/";
      const parent = getOrCreateDir(parentPath);
      parent.children.push(node);
    }

    return node;
  };

  for (const entry of entries) {
    if (entry.is_dir) {
      getOrCreateDir(entry.path);
      continue;
    }

    const parts = entry.path.split("/");
    const name = parts[parts.length - 1];
    const fileNode: TreeNode = {
      name,
      path: entry.path,
      is_dir: false,
      size: entry.size,
      compressed_size: entry.compressed_size,
      children: [],
    };

    if (parts.length === 1) {
      root.push(fileNode);
    } else {
      const parentPath = parts.slice(0, -1).join("/") + "/";
      const parent = getOrCreateDir(parentPath);
      parent.children.push(fileNode);
    }
  }

  // 目录优先 + 字母序. 递归排所有层.
  const sortNodes = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const node of nodes) {
      if (node.children.length > 0) sortNodes(node.children);
    }
  };
  sortNodes(root);

  return root;
}

function formatSize(bytes: number): string {
  if (bytes === 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function TreeItem({
  node,
  depth,
  expanded,
  onToggle,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (path: string) => void;
}) {
  const isOpen = expanded.has(node.path);

  if (node.is_dir) {
    return (
      <>
        <button
          type="button"
          onClick={() => onToggle(node.path)}
          className="flex w-full items-center gap-1.5 rounded-[4px] px-1 py-0.5 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]"
          style={{ paddingLeft: `${depth * 16 + 4}px` }}
        >
          <ChevronRight
            size={13}
            className={cn(
              "shrink-0 text-[color:var(--color-ink-dim)] transition-transform",
              isOpen && "rotate-90",
            )}
          />
          {isOpen ? (
            <FolderOpen size={14} className="shrink-0 text-[color:var(--color-thread-file)]" />
          ) : (
            <Folder size={14} className="shrink-0 text-[color:var(--color-thread-file)]" />
          )}
          <span className="truncate text-[12px] text-[color:var(--color-paper-dim)]">
            {node.name}
          </span>
        </button>
        {isOpen &&
          node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
            />
          ))}
      </>
    );
  }

  return (
    <div
      className="flex w-full items-center gap-1.5 px-1 py-0.5"
      // depth*16 + 4 (跟 dir 对齐) + 18 (chevron+gap 抵消; dir 那行有 chevron, file 没有)
      style={{ paddingLeft: `${depth * 16 + 4 + 18}px` }}
    >
      <File size={14} className="shrink-0 text-[color:var(--color-ink-dim)]" />
      <span className="flex-1 truncate text-[12px] text-[color:var(--color-paper-dim)]">
        {node.name}
      </span>
      <span className="shrink-0 font-mono text-[10.5px] tabular-nums text-[color:var(--color-ink-dim)]">
        {formatSize(node.size)}
      </span>
    </div>
  );
}

export function ArchivePreview({ sessionId, path }: Props) {
  const [data, setData] = useState<ArchivePreviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    setExpanded(new Set());

    api
      .getPreviewArchive(sessionId, path)
      .then((result) => {
        if (cancelled) return;
        setData(result);
        // 第一层目录默认展开 — 跟 krow 一致.
        const firstLevel = new Set<string>();
        for (const entry of result.entries) {
          if (!entry.is_dir) continue;
          const depth = entry.path.replace(/\/$/, "").split("/").length;
          if (depth === 1) firstLevel.add(entry.path);
        }
        setExpanded(firstLevel);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "zip 加载失败");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId, path]);

  const tree = useMemo(() => (data ? buildTree(data.entries) : []), [data]);

  const onToggle = useCallback((p: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  }, []);

  async function openWithSystem() {
    try {
      await api.openFile({ sessionId, path });
    } catch (err) {
      toast.error(`Open failed: ${String(err)}`);
    }
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <div className="text-[12px] text-[color:var(--color-error)]">
          zip 预览失败: {error}
        </div>
        <button
          type="button"
          onClick={openWithSystem}
          className="flex items-center gap-1.5 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] px-3 py-1.5 text-[12px] text-[color:var(--color-paper-dim)] transition-colors hover:border-[color:var(--color-line-strong)] hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
        >
          <ExternalLink size={11} />
          Open with default app
        </button>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="flex h-full items-center justify-center text-[color:var(--color-ink)]">
        <Loader2 size={14} className="animate-spin" />
      </div>
    );
  }

  // 文件数 (不计目录, 直观一些).
  const fileCount = data.entries.filter((e) => !e.is_dir).length;

  return (
    <div className="flex h-full min-h-0 flex-col bg-[color:var(--color-bg-card)]">
      {/* 顶部统计栏 */}
      <div className="flex shrink-0 items-center gap-2 border-b border-[color:var(--color-line-soft)] bg-[color:var(--color-bg-soft)] px-3 py-1.5 text-[11px] text-[color:var(--color-ink)]">
        <Package size={12} className="text-[color:var(--color-ink-dim)]" />
        <span>
          {fileCount} {fileCount === 1 ? "file" : "files"}
          {" · "}
          {formatSize(data.size)}
        </span>
        {data.truncated && (
          <span className="ml-auto font-mono text-[10px] text-[color:var(--color-ink-dim)]">
            truncated · {data.total_entries} entries total
          </span>
        )}
      </div>

      {/* 文件树 */}
      <div className="scrollbar-hidden min-h-0 flex-1 overflow-auto py-1">
        {tree.map((node) => (
          <TreeItem
            key={node.path}
            node={node}
            depth={0}
            expanded={expanded}
            onToggle={onToggle}
          />
        ))}
      </div>
    </div>
  );
}
