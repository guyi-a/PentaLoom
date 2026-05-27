// 用户消息气泡 — 跟 assistant 的 frames 区分开. 简洁: 右对齐 + 朴素卡片.
//
// 用户的"第一句话"或后续追问会进 history (role=user), 在历史里以这种形式呈现.
// 现场流里, 用户刚刚发出去的 prompt 也用这个组件提前显示 (在 assistant frames 之前).

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-[8px] bg-[color:var(--color-accent)]/8 px-3.5 py-2 text-[14px] leading-relaxed text-[color:var(--color-paper)] ring-1 ring-[color:var(--color-accent)]/15">
        {text}
      </div>
    </div>
  );
}
