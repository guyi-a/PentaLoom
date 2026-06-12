// 通用文件树 — dumb 渲染. 上层 (WorkspaceTree) 控 expanded Set + 数据 + click handler.
//
// 设计:
//   - 自己用 <div> 递归 + ChevronRight 转 90° (不引 Radix Collapsible, 跟 PentaLoom minimal 风格一致)
//   - 文件夹 click 切 expanded (受控 — onToggle 上抛)
//   - 文件 click → onSelectFile (上层走 openPreview)
//   - selectedPath 高亮 (跟 previewFile 联动)
//   - 文件夹 icon 用 lucide Folder/FolderOpen, 文件 icon 走 iconForExt
//   - 排序后端做 (folder 优先 + alphabetic), 前端只渲

import { ChevronRight, Folder, FolderOpen } from "lucide-react";

import type { FsTreeNode } from "@/lib/types";
import { extOf, iconForExt } from "@/lib/tool-meta";
import { cn } from "@/lib/utils";

interface Props {
  /** 树根的 children. 根节点本身一般是 mount/sandbox 路径, 由 WorkspaceTree 自己渲. */
  nodes: FsTreeNode[];
  /** 当前展开的目录绝对路径集合. 受控 — toggle 走 onToggle 上抛. */
  expanded: Set<string>;
  onToggle: (path: string) => void;
  /** 文件 click → 上层 openPreview. */
  onSelectFile: (node: FsTreeNode) => void;
  /** 高亮当前 preview 中的文件. 从 previewFile.path 来. */
  selectedPath?: string | null;
  /** 缩进层级 — 内部递归用, 外部别传. */
  depth?: number;
}

export function FileTree({
  nodes,
  expanded,
  onToggle,
  onSelectFile,
  selectedPath,
  depth = 0,
}: Props) {
  if (nodes.length === 0) return null;
  return (
    <ul className="space-y-px">
      {nodes.map((node) => (
        <FileTreeNodeRow
          key={node.path}
          node={node}
          expanded={expanded}
          onToggle={onToggle}
          onSelectFile={onSelectFile}
          selectedPath={selectedPath}
          depth={depth}
        />
      ))}
    </ul>
  );
}

interface RowProps {
  node: FsTreeNode;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  onSelectFile: (node: FsTreeNode) => void;
  selectedPath?: string | null;
  depth: number;
}

function FileTreeNodeRow({
  node,
  expanded,
  onToggle,
  onSelectFile,
  selectedPath,
  depth,
}: RowProps) {
  const isOpen = expanded.has(node.path);
  const isSelected = selectedPath === node.path;
  const indentPx = 8 + depth * 12;

  if (node.is_directory) {
    return (
      <li>
        <button
          type="button"
          onClick={() => onToggle(node.path)}
          className={cn(
            "group flex w-full items-center gap-1 rounded-[3px] py-0.5 pr-1 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]",
            isSelected && "bg-[color:var(--color-bg-raised)]",
          )}
          style={{ paddingLeft: indentPx }}
          title={node.path}
        >
          <ChevronRight
            size={11}
            className={cn(
              "shrink-0 text-[color:var(--color-ink-dim)] transition-transform",
              isOpen && "rotate-90",
            )}
          />
          {isOpen ? (
            <FolderOpen
              size={12}
              className="shrink-0 text-[color:var(--color-thread-file)]"
            />
          ) : (
            <Folder
              size={12}
              className="shrink-0 text-[color:var(--color-thread-file)]"
            />
          )}
          <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-[color:var(--color-paper-dim)] group-hover:text-[color:var(--color-paper)]">
            {node.name}
          </span>
        </button>
        {isOpen && node.children && node.children.length > 0 && (
          <FileTree
            nodes={node.children}
            expanded={expanded}
            onToggle={onToggle}
            onSelectFile={onSelectFile}
            selectedPath={selectedPath}
            depth={depth + 1}
          />
        )}
        {isOpen && node.truncated && (
          <div
            className="font-mono text-[10px] italic text-[color:var(--color-ink-dim)]"
            style={{ paddingLeft: indentPx + 24 }}
          >
            …更多 (深度上限)
          </div>
        )}
      </li>
    );
  }

  // 文件
  const ext = extOf(node.name);
  const Icon = iconForExt(ext);
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelectFile(node)}
        className={cn(
          "group flex w-full items-center gap-1 rounded-[3px] py-0.5 pr-1 text-left transition-colors hover:bg-[color:var(--color-bg-raised)]",
          isSelected && "bg-[color:var(--color-bg-raised)]",
        )}
        style={{ paddingLeft: indentPx + 12 /* chevron 留白对齐 */ }}
        title={node.path}
      >
        <Icon
          size={11}
          className="shrink-0 text-[color:var(--color-thread-file)] opacity-70"
        />
        <span
          className={cn(
            "min-w-0 flex-1 truncate font-mono text-[11.5px] group-hover:text-[color:var(--color-paper)]",
            isSelected
              ? "text-[color:var(--color-paper)]"
              : "text-[color:var(--color-paper-dim)]",
          )}
        >
          {node.name}
        </span>
      </button>
    </li>
  );
}
