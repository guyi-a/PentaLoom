// 现场 SSE 流的 reducer: 把后端推过来的一帧合并进当前 turn 的 frames 数组.
//
// 幂等 — resume 时后端 stream_all 会把已发的 text_delta / thinking_delta 折叠成
// 完整 text / thinking frame (streaming=true) 重放, 之后续订阅段才是 raw delta.
// 多次 resume 同一 turn 必须输出同样的 liveFrames, 不能双份.
//
// 合并规则按 (msg_uuid, index):
//   - 完整 text / thinking frame: 找已有同 (uuid, index), 有则 overwrite (重放
//     时新内容比旧的长), 无则 push.
//   - text_delta / thinking_delta: 找已有同 (uuid, index, kind) frame, 有则
//     append text, 无则新建 streaming=true frame.
//   - 其它类型 (tool_use / tool_result / task_* / result / error / stream_end):
//     按 frame 自身的 stable id (tool_use_id / task_id 等) 或 (msg_uuid, index)
//     去重; 找不到唯一 key 就直接 push.
//
// 老回落: 后端给 msg_uuid 是 SDK 字段, 理论上 streaming 期间是 SDKPartialAssistantMessage
// 的 uuid, finalized 后是 AssistantMessage 的 uuid — 这两个**不一定相同**.
// 所以 (msg_uuid, index) 不能跨"重放完整 frame ↔ 后续 raw delta"对齐.
// 实测设计: stream_all 重放阶段 = 完整 frame; 续订阅阶段 = raw delta. 二者各自
// 内部按 (uuid, index) 幂等就足够 — 续订阅段的 delta 是 fresh stream, 没有重放
// 来源, 不会跟重放阶段的完整 frame 撞 key.

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
  if (f.type === "text" || f.type === "thinking") {
    return upsertCompleteFrame(prev, f);
  }
  if (f.type === "tool_use") return upsertById(prev, f, "id");
  if (f.type === "tool_result") return upsertById(prev, f, "tool_use_id");
  // 任何其它非 delta 帧 push 进来 → 之前所有 streaming text/thinking 段都不再可能续了
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
  // 先按 (msg_uuid, index) 找已有同 kind 的 frame — 续订阅段的 raw delta 要
  // 追加到重放阶段折叠出来的完整 frame 上.
  const idx = prev.findIndex(
    (x) =>
      x.type === kind &&
      x.msg_uuid === d.msg_uuid &&
      x.index === d.index,
  );
  if (idx !== -1) {
    const existing = prev[idx] as TextFrame | ThinkingFrame;
    const next = prev.slice();
    next[idx] = { ...existing, text: existing.text + d.text, streaming: true };
    return next;
  }
  // 没找到 → 新建. 先 settle 旧的同类型 streaming, 防多个 caret 同时闪.
  const fresh: TextFrame | ThinkingFrame = {
    type: kind,
    text: d.text,
    msg_uuid: d.msg_uuid,
    index: d.index,
    streaming: true,
  };
  return [...settleAll(prev), fresh];
}

function upsertCompleteFrame(
  prev: Frame[],
  f: TextFrame | ThinkingFrame,
): Frame[] {
  // 完整 text / thinking frame: 按 (msg_uuid, index) 覆盖
  if (f.msg_uuid && f.index !== undefined) {
    const idx = prev.findIndex(
      (x) =>
        x.type === f.type &&
        x.msg_uuid === f.msg_uuid &&
        x.index === f.index,
    );
    if (idx !== -1) {
      const next = prev.slice();
      next[idx] = f;
      return next;
    }
  }
  return [...settleAll(prev), f];
}

function upsertById<K extends "id" | "tool_use_id">(
  prev: Frame[],
  f: Extract<Frame, { type: "tool_use" | "tool_result" }>,
  key: K,
): Frame[] {
  // tool_use 按 id 去重 (id == tool_use_id, SDK 全局唯一)
  // tool_result 按 tool_use_id 去重 (一个 tool 调用一个 result)
  const target = (f as unknown as Record<K, string>)[key];
  const idx = prev.findIndex(
    (x) => x.type === f.type && (x as unknown as Record<K, string>)[key] === target,
  );
  if (idx !== -1) {
    const next = prev.slice();
    next[idx] = f;
    return next;
  }
  return [...settleAll(prev), f];
}
