// 现场 SSE 流的 reducer: 把后端推过来的一帧合并进当前 turn 的 frames 数组.
//
// 主要任务: text_delta / thinking_delta 帧不能直接 push, 它要拼到"上一个同
// 类型的 streaming frame"上, 否则会出现成百上千条单字 block.
//
// 不依赖 msg_uuid 匹配 — SDK 的 SDKPartialAssistantMessage 给每个 chunk 都分配
// 了独立 uuid, 没办法用 uuid 串起来. 但 delta 是有序到达的, 只要看末尾那帧:
// 如果是同 kind 的 streaming frame → 拼上去; 否则 (比如末尾是 tool_use / 不同 kind
// / 没 streaming) → 新建一帧. 这天然处理了 text → tool_use → text 这种打断.
//
// 其它类型帧 (tool_use / tool_result / task_* / result / error) 直接 push.

import type {
  Frame,
  TextDeltaFrame,
  TextFrame,
  ThinkingDeltaFrame,
  ThinkingFrame,
} from "./types";

export function appendFrame(prev: Frame[], f: Frame): Frame[] {
  if (f.type === "text_delta") return mergeDelta(prev, f, "text");
  if (f.type === "thinking_delta") return mergeDelta(prev, f, "thinking");
  // 任何非 delta 帧 push 进来 → 之前所有 streaming text/thinking 段都不再可能续了
  // (它们已经不是数组末尾, 后续 delta 也只会去匹配新末尾的同类型). 一并 settle, 停闪.
  // result/error/stream_end 走同一路径, 自然带入.
  return [...settleAll(prev), f];
}

function settleAll(prev: Frame[]): Frame[] {
  let changed = false;
  const next = prev.map((x) => {
    if ((x.type === "text" || x.type === "thinking") && x.streaming) {
      changed = true;
      return { ...x, streaming: false };
    }
    return x;
  });
  return changed ? next : prev;
}

function mergeDelta(
  prev: Frame[],
  d: TextDeltaFrame | ThinkingDeltaFrame,
  kind: "text" | "thinking",
): Frame[] {
  const last = prev[prev.length - 1];
  if (last && last.type === kind && last.streaming) {
    const next = prev.slice();
    next[next.length - 1] = { ...last, text: last.text + d.text };
    return next;
  }
  // 末尾不是同类型 streaming → 开新帧之前先 settle 旧的, 防止多个 caret 同时闪
  const fresh: TextFrame | ThinkingFrame = {
    type: kind,
    text: d.text,
    msg_uuid: d.msg_uuid,
    index: d.index,
    streaming: true,
  };
  return [...settleAll(prev), fresh];
}
