// 聊天主区: 历史 + 流式帧 + 输入框. 全部 HITL 工具都走内联审批 (在 ToolRow
// 卡片底部展开按钮条), 不再有任何模态弹窗 — 视觉上不打断对话流.
//
// 数据流:
// - historyMessages: 进入页面时一次性加载, 静态.
// - streamedFrames: 当前正在进行的 turn, 由父组件 (ChatPage / EmptyPage) 推过来.
// - tool_use / tool_result 在渲染前先按 tool_use_id 配对成 ToolPair → <ToolRow>,
//   单行 chip + 可展开内容. 其它帧仍走 <FrameBlock>.
// - 任何 HITL 工具的 tool_use 帧出现且没对应 tool_result → 算 pending, 在对应
//   ToolRow 卡片底部展开审批条. 用户点选 → POST /chat/permission/... → 后端
//   resolve future → agent 继续推进 → tool_result 帧到达 → pending 消失 →
//   ToolRow 自动收起 (用户没手动 toggle 过的话).

import { useEffect, useMemo, useRef, useState } from "react";

import { FrameBlock } from "./FrameBlock";
import { ToolRow, type ToolPair } from "./ToolRow";
import { UserBubble } from "./UserBubble";
import { PromptInput } from "./PromptInput";
import type { Frame, HistoryMessage, ToolResultFrame } from "@/lib/types";
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

// 把 tool_use 跟 tool_result 按 tool_use_id 配对, 平铺成 (Frame | ToolPair) 列表.
// 顺序保持 tool_use 出现位置 — result 来了只是塞回去, 不重排.
//
// extResults: 跨 message 的 tool_result 池. 历史里 tool_use 在 assistant message,
// tool_result 在紧跟的 user message — 单 message 内 pair 不到, 全栈历史会显示
// "所有工具都在转圈". 传入全局 map 让 use 能查到外部 result.
type RenderItem =
  | { kind: "frame"; frame: Frame; key: string }
  | { kind: "pair"; pair: ToolPair; key: string };

function pairFrames(
  frames: Frame[],
  extResults?: Map<string, ToolResultFrame>,
): RenderItem[] {
  const items: RenderItem[] = [];
  const pairIdxById = new Map<string, number>();
  for (let i = 0; i < frames.length; i++) {
    const f = frames[i];
    if (f.type === "tool_use") {
      // 先查 extResults — 历史跨 message 的 result 已落在那里
      const ext = extResults?.get(f.id) ?? null;
      pairIdxById.set(f.id, items.length);
      items.push({
        kind: "pair",
        pair: { use: f, result: ext },
        key: `tool:${f.id}`,
      });
    } else if (f.type === "tool_result") {
      const idx = pairIdxById.get(f.tool_use_id);
      if (idx !== undefined) {
        const cur = items[idx];
        if (cur.kind === "pair") {
          // 替换为带 result 的新对象 — React 会按 key 复用 ToolRow, prop 变化即可.
          items[idx] = {
            kind: "pair",
            pair: { use: cur.pair.use, result: f },
            key: cur.key,
          };
        }
      } else if (extResults?.has(f.tool_use_id)) {
        // 这条 result 对应的 use 在别的 message 里, 已经通过 extResults 在那边配上了 — 这里别重复渲染
      } else {
        // 真孤儿 — 罕见但别挂掉
        items.push({
          kind: "frame",
          frame: f,
          key: `orphan_result:${f.tool_use_id}:${i}`,
        });
      }
    } else {
      items.push({ kind: "frame", frame: f, key: frameKey(f, i) });
    }
  }
  return items;
}

// 全局扫历史, 把所有 tool_result 按 tool_use_id 索引. 给 MessageGroup 渲染
// assistant turn 时查跨 message 的 result 用.
function buildHistoryResultMap(
  history: HistoryMessage[],
): Map<string, ToolResultFrame> {
  const m = new Map<string, ToolResultFrame>();
  for (const msg of history) {
    for (const f of msg.frames) {
      if (f.type === "tool_result") {
        m.set(f.tool_use_id, f);
      }
    }
  }
  return m;
}

// user message 是不是纯 tool_result (没 text) — 是的话整条不渲染, 不然会显示
// 一个空的 UserBubble 占位.
function isPureToolResultMessage(message: HistoryMessage): boolean {
  if (message.role !== "user") return false;
  let hasText = false;
  let hasResult = false;
  for (const f of message.frames) {
    if (f.type === "text" && f.text.trim()) hasText = true;
    if (f.type === "tool_result") hasResult = true;
  }
  return hasResult && !hasText;
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
  // 规则: tool_use(name ∈ TOOLS_NEEDING_APPROVAL) 之后没对应 tool_result
  // **或** permission_resolved 就算 pending. permission_resolved 是 POST
  // /chat/permission 后端立刻发的, 让审批栏 (按钮) 在工具实际执行前就消失;
  // tool_result 是工具跑完才到, 中间几秒~几分钟靠 permission_resolved 提前
  // dismiss. 把所有 pending id 收集成 set, 透给 ToolRow 决定是否展开审批条.
  const pendingApprovalIds = useMemo<Set<string>>(() => {
    const open = new Set<string>();
    const resolved = new Set<string>();
    for (const f of visibleStreamed) {
      if (f.type === "tool_use" && TOOLS_NEEDING_APPROVAL.includes(f.name)) {
        open.add(f.id);
      } else if (f.type === "tool_result") {
        resolved.add(f.tool_use_id);
      } else if (f.type === "permission_resolved") {
        resolved.add(f.tool_use_id);
      }
    }
    for (const id of resolved) open.delete(id);
    return open;
  }, [visibleStreamed]);

  // 流帧配对成 items — useMemo, 避免每次渲染都重算
  const streamedItems = useMemo(() => pairFrames(visibleStreamed), [visibleStreamed]);

  // 历史里 tool_result 跨 message 的全局索引 — 给 MessageGroup 跨 message 配对用
  const historyResultMap = useMemo(
    () => buildHistoryResultMap(historyMessages),
    [historyMessages],
  );

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
        className="scrollbar-hidden flex-1 overflow-y-auto"
      >
        <div className="mx-auto max-w-[820px] space-y-4 px-6 py-8">
          {/* 历史 — 先全局扫一遍 tool_result, 让 MessageGroup 渲染时能跨 message 配对.
              否则 assistant message 里的 tool_use 配不上紧跟 user message 里的
              tool_result, 全栈历史所有 chip 都停在 in-progress 转圈. */}
          {historyMessages.map((m) =>
            isPureToolResultMessage(m) ? null : (
              <MessageGroup
                key={m.uuid}
                message={m}
                extResults={historyResultMap}
              />
            ),
          )}

          {/* 本轮用户 prompt — 历史里可能没有 (新建会话时, 后端写 jsonl 是异步的) */}
          {localUserPrompt && (
            <div className="space-y-3">
              <UserBubble text={localUserPrompt} />
            </div>
          )}

          {/* 现场流帧 (跨源去重 + tool_use/tool_result 已配对) */}
          {streamedItems.length > 0 && (
            <div className="space-y-3">
              {streamedItems.map((item) =>
                item.kind === "pair" ? (
                  <ToolRow
                    key={item.key}
                    pair={item.pair}
                    sessionId={sessionId}
                    pendingApproval={pendingApprovalIds.has(item.pair.use.id)}
                  />
                ) : (
                  <FrameBlock key={item.key} frame={item.frame} />
                ),
              )}
            </div>
          )}

          {/* 本轮 user prompt 已发出, 但后端首帧还没到 — 给个占位, 别让用户对着空白等 */}
          {localUserPrompt && streamedItems.length === 0 && (
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
        placeholder={inputDisabled ? "Sending…" : "Continue the thread…"}
      />
    </div>
  );
}

// 历史里一条消息 (一个 user/assistant turn) 的渲染. tool_use/result 在 history 里
// 可能跨 message: assistant 的 tool_use 配紧跟 user message 的 tool_result.
// 父组件传 extResults (全历史的 tool_use_id → result map), pairFrames 用它跨 message
// 配对; 没传时退化成单 message 内配对 (跟 live 流一样).
function MessageGroup({
  message,
  extResults,
}: {
  message: HistoryMessage;
  extResults?: Map<string, ToolResultFrame>;
}) {
  if (message.role === "user") {
    // user 历史: frames 里可能有 text + tool_result; tool_result 已被
    // extResults 收走并配到对应 assistant 的 ToolRow 上, 这里只渲染 text.
    const text = message.frames
      .filter((f): f is { type: "text"; text: string } => f.type === "text")
      .map((f) => f.text)
      .join("\n");
    if (!text) return null;
    return <UserBubble text={text} />;
  }
  // assistant: 一条 turn 里可能有 text/thinking/tool_use/tool_result 混合.
  // 传 extResults 让 pairFrames 跨 message 拿 result.
  const items = pairFrames(message.frames, extResults);
  return (
    <div className="space-y-3">
      {items.map((item) =>
        item.kind === "pair" ? (
          <ToolRow key={item.key} pair={item.pair} />
        ) : (
          <FrameBlock key={item.key} frame={item.frame} />
        ),
      )}
    </div>
  );
}

// React key 优先用 frame 自带的稳定 id, 否则 fallback index. 重放幂等后
// 同一 frame 在 liveFrames / historyMessages 之间被覆盖时, key 不变 → React 复用
// 节点, 不闪动.
function frameKey(f: Frame, fallback: number): string {
  if (f.type === "task_started" || f.type === "task_progress" || f.type === "task_done") {
    return `${f.type}:${f.task_id}`;
  }
  if ((f.type === "text" || f.type === "thinking") && f.msg_uuid !== undefined) {
    return `${f.type}:${f.msg_uuid}:${f.index ?? 0}`;
  }
  return `${f.type}:${fallback}`;
}
