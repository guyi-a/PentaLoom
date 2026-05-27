// 聊天主区: 历史 + 流式帧 + 输入框. 全部 HITL 工具都走内联审批 (在 FrameBlock
// 的 ToolUseBlock 卡片底部展开按钮条), 不再有任何模态弹窗 — 视觉上不打断对话流.
//
// 数据流:
// - historyMessages: 进入页面时一次性加载, 静态.
// - streamedFrames: 当前正在进行的 turn, 由父组件 (ChatPage / EmptyPage) 推过来.
// - 任何 HITL 工具 (Bash / install_libs / run_script / workspace) 的 tool_use 帧
//   出现且没对应 tool_result → 算 pending, 把 id 收集到 pendingApprovalIds, 透给
//   FrameBlock 让对应 ToolUseBlock 在卡片底部展开审批条. 用户点选 → POST
//   /chat/permission/... → 后端 resolve future → agent 继续推进 → tool_result
//   帧到达 → pending 消失 → 审批条收起.

import { useEffect, useMemo, useRef, useState } from "react";

import { FrameBlock } from "./FrameBlock";
import { UserBubble } from "./UserBubble";
import { PromptInput } from "./PromptInput";
import type { Frame, HistoryMessage } from "@/lib/types";
import { TOOLS_NEEDING_APPROVAL } from "@/lib/types";

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

export function ChatStream({
  sessionId,
  historyMessages,
  streamedFrames,
  localUserPrompt,
  onUserSend,
  inputDisabled,
}: Props) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  // ── 跨源去重: liveFrames 里 msg_uuid 已经出现在历史里的, 不要再渲染一遍 ──
  // 触发场景: turn 跑到一半 → ModelMessageComplete → SDK flush 进 JSONL → SWR
  // 重新 fetch /messages → 但 buffer.chunks 还没清 → liveFrames 里也有同 message.
  // 不去重就会上下双份. msg_uuid 用的是 anthropic message.id (msg_xxxx), 历史
  // 接口给的 message_id 同源, 直接 set 过滤即可.
  const visibleStreamed = useMemo(() => {
    if (historyMessages.length === 0) return streamedFrames;
    const historyMsgIds = new Set(
      historyMessages
        .map((m) => m.message_id)
        .filter((x): x is string => !!x),
    );
    if (historyMsgIds.size === 0) return streamedFrames;
    return streamedFrames.filter((f) => {
      const uuid = (f as { msg_uuid?: string | null }).msg_uuid;
      return !uuid || !historyMsgIds.has(uuid);
    });
  }, [historyMessages, streamedFrames]);

  // ── 收集所有 pending 的 HITL tool_use id ─────────────────────
  // 规则: tool_use(name ∈ TOOLS_NEEDING_APPROVAL) 之后没对应 tool_result 就算
  // pending. 把所有 pending id 收集成 set, 透给 FrameBlock 让对应 ToolUseBlock
  // 在卡片底部展开审批条. 一轮 turn 内 agent 可能连发多条审批 (e.g. 多个 Bash),
  // 互不挡住, 全部内联展示, 不再弹模态.
  const pendingApprovalIds = useMemo<Set<string>>(() => {
    const open = new Set<string>();
    const resolved = new Set<string>();
    for (const f of visibleStreamed) {
      if (f.type === "tool_use" && TOOLS_NEEDING_APPROVAL.includes(f.name)) {
        open.add(f.id);
      } else if (f.type === "tool_result") {
        resolved.add(f.tool_use_id);
      }
    }
    for (const id of resolved) open.delete(id);
    return open;
  }, [visibleStreamed]);

  // ── 自动滚到底 (除非用户手动往上滚了) ────────────────────────
  const [autoScroll, setAutoScroll] = useState(true);
  useEffect(() => {
    if (!autoScroll) return;
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [historyMessages, visibleStreamed, localUserPrompt, autoScroll]);

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

          {/* 现场流帧 (跨源去重后) */}
          {visibleStreamed.length > 0 && (
            <div className="space-y-3">
              {visibleStreamed.map((f, i) => (
                <FrameBlock
                  key={frameKey(f, i)}
                  frame={f}
                  sessionId={sessionId}
                  pendingApproval={
                    f.type === "tool_use" && pendingApprovalIds.has(f.id)
                  }
                />
              ))}
            </div>
          )}

          {/* 本轮 user prompt 已发出, 但后端首帧还没到 — 给个占位, 别让用户对着空白等 */}
          {localUserPrompt && visibleStreamed.length === 0 && (
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
        <FrameBlock key={frameKey(f, i)} frame={f} />
      ))}
    </div>
  );
}

// React key 优先用 frame 自带的稳定 id, 否则 fallback index. 重放幂等后
// 同一 frame 在 liveFrames / historyMessages 之间被覆盖时, key 不变 → React 复用
// 节点, 不闪动.
function frameKey(f: Frame, fallback: number): string {
  if (f.type === "tool_use") return `tool_use:${f.id}`;
  if (f.type === "tool_result") return `tool_result:${f.tool_use_id}`;
  if (f.type === "task_started" || f.type === "task_progress" || f.type === "task_done") {
    return `${f.type}:${f.task_id}`;
  }
  if ((f.type === "text" || f.type === "thinking") && f.msg_uuid !== undefined) {
    return `${f.type}:${f.msg_uuid}:${f.index ?? 0}`;
  }
  return `${f.type}:${fallback}`;
}
