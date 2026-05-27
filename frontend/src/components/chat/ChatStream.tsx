// 聊天主区: 历史 + 流式帧 + 输入框 + workspace 授权弹窗.
//
// 数据流:
// - historyMessages: 进入页面时一次性加载, 静态.
// - streamedFrames: 当前正在进行的 turn, 由父组件 (ChatPage / EmptyPage) 推过来.
// - 当 streamedFrames 里出现 mcp__pentaloom__request_workspace_dir 的 tool_use 帧, 弹窗显示.
//   弹窗的"允许 / 拒绝"会 POST /chat/permission/:tool_use_id, 后端 resolve future,
//   agent 继续推进, tool_result 帧会自然到达, 弹窗收起.

import { useEffect, useMemo, useRef, useState } from "react";

import { FrameBlock } from "./FrameBlock";
import { UserBubble } from "./UserBubble";
import { WorkspacePermissionDialog } from "@/components/permission/WorkspacePermissionDialog";
import { PromptInput } from "./PromptInput";
import type {
  Frame,
  HistoryMessage,
  ToolUseFrame,
} from "@/lib/types";
import {
  BASH_TOOL_NAME,
  WORKSPACE_PERMISSION_TOOL_NAME,
} from "@/lib/types";

interface Props {
  sessionId: string;
  historyMessages: HistoryMessage[];
  // 当前 turn 的实时帧. 父组件每来一帧就 push 一次. 也支持 EmptyPage 把它当作"刚结束的 turn".
  streamedFrames: Frame[];
  // 用户在本 turn 刚发出的 prompt — 显示在 streamedFrames 之前 (历史里不需要传, 历史里 user 是 role)
  localUserPrompt?: string | null;
  // 父组件实现的"再发一条" — ChatPage 会真的开新 SSE, EmptyPage 传 noop / 跳转
  onUserSend: (prompt: string) => void;
  inputDisabled?: boolean;
}

interface PendingPermission {
  toolUseId: string;
  path: string;
  reason: string;
}

export function ChatStream({
  sessionId,
  historyMessages,
  streamedFrames,
  localUserPrompt,
  onUserSend,
  inputDisabled,
}: Props) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  // ── 检测 workspace 授权请求 ─────────────────────────────────
  // 规则: 在 streamedFrames 里找最后一个 tool_use(name=mcp__pentaloom__request_workspace_dir),
  // 如果它后面没有对应的 tool_result, 就是 pending — 弹窗.
  const pending = useMemo<PendingPermission | null>(() => {
    let lastRequest: ToolUseFrame | null = null;
    const resolvedIds = new Set<string>();
    for (const f of streamedFrames) {
      if (
        f.type === "tool_use" &&
        f.name === WORKSPACE_PERMISSION_TOOL_NAME
      ) {
        lastRequest = f;
      } else if (f.type === "tool_result") {
        resolvedIds.add(f.tool_use_id);
      }
    }
    if (!lastRequest || resolvedIds.has(lastRequest.id)) return null;
    return {
      toolUseId: lastRequest.id,
      path: String(lastRequest.input.path ?? ""),
      reason: String(lastRequest.input.reason ?? ""),
    };
  }, [streamedFrames]);

  // ── 检测 Bash 内联审批 ──────────────────────────────────────
  // 跟 workspace 一样: tool_use(Bash) 没对应 tool_result 就算 pending. 但 Bash
  // 不是模态, 不需要"最后一个", 而是把所有 pending id 收集成 set, 让 FrameBlock
  // 按 id 自查. 一轮 turn 内 agent 可以连发多条 Bash, 互不挡住别的.
  const pendingBashIds = useMemo<Set<string>>(() => {
    const open = new Set<string>();
    const resolved = new Set<string>();
    for (const f of streamedFrames) {
      if (f.type === "tool_use" && f.name === BASH_TOOL_NAME) {
        open.add(f.id);
      } else if (f.type === "tool_result") {
        resolved.add(f.tool_use_id);
      }
    }
    for (const id of resolved) open.delete(id);
    return open;
  }, [streamedFrames]);

  // ── 自动滚到底 (除非用户手动往上滚了) ────────────────────────
  const [autoScroll, setAutoScroll] = useState(true);
  useEffect(() => {
    if (!autoScroll) return;
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [historyMessages, streamedFrames, localUserPrompt, autoScroll]);

  function onScroll() {
    const el = scrollerRef.current;
    if (!el) return;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    setAutoScroll(atBottom);
  }

  return (
    <div className="flex h-full flex-col">
      {/* 滚动区 */}
      <div
        ref={scrollerRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto"
      >
        <div className="mx-auto max-w-[820px] space-y-4 px-6 py-8">
          {/* 历史 */}
          {historyMessages.map((m) => (
            <MessageGroup key={m.uuid} message={m} />
          ))}

          {/* 本轮用户 prompt — 历史里可能没有 (新建会话时, 后端写 jsonl 是异步的) */}
          {localUserPrompt && (
            <div className="space-y-3">
              <UserBubble text={localUserPrompt} />
            </div>
          )}

          {/* 现场流帧 */}
          {streamedFrames.length > 0 && (
            <div className="space-y-3">
              {streamedFrames.map((f, i) => (
                <FrameBlock
                  key={i}
                  frame={f}
                  sessionId={sessionId}
                  pendingApproval={
                    f.type === "tool_use" && pendingBashIds.has(f.id)
                  }
                />
              ))}
            </div>
          )}

          {/* 本轮 user prompt 已发出, 但后端首帧还没到 — 给个占位, 别让用户对着空白等 */}
          {localUserPrompt && streamedFrames.length === 0 && (
            <div className="flex items-center gap-2 px-1 py-2 text-[12px] text-[color:var(--color-ink)]">
              <span className="inline-flex h-1.5 w-1.5 animate-pulse rounded-full bg-[color:var(--color-accent)]" />
              <span>Working on it…</span>
            </div>
          )}
        </div>
      </div>

      {/* 输入框 */}
      <PromptInput
        onSend={onUserSend}
        disabled={inputDisabled}
        placeholder={
          inputDisabled
            ? "Sending…"
            : "Ask anything (Shift+Enter for new line)"
        }
      />

      {/* 授权弹窗 — pending 时一定显示, 用户必须做选择 */}
      {pending && (
        <WorkspacePermissionDialog
          sessionId={sessionId}
          toolUseId={pending.toolUseId}
          path={pending.path}
          reason={pending.reason}
        />
      )}
    </div>
  );
}

// 历史里一条消息 (一个 user/assistant turn) 的渲染
function MessageGroup({ message }: { message: HistoryMessage }) {
  if (message.role === "user") {
    // user 历史: frames 里基本就一个 text
    const text = message.frames
      .filter((f): f is { type: "text"; text: string } => f.type === "text")
      .map((f) => f.text)
      .join("\n");
    if (!text) return null;
    return <UserBubble text={text} />;
  }
  // assistant: 一条 turn 里可能有 text/thinking/tool_use/tool_result 混合
  return (
    <div className="space-y-3">
      {message.frames.map((f, i) => (
        <FrameBlock key={i} frame={f} />
      ))}
    </div>
  );
}
