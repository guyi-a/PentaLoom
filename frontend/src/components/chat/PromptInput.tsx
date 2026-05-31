// 底部输入条 — 跟 EmptyPage 那个起始卡片同款 (圆角卡 + 上 textarea + 下工具栏).
// 风格基线: Nordic Light, 单色描边, focus-within 描边变 accent + 微 ring.
//
// 键位:
//   Enter         → 发送        (主行为, 跟 Claude / ChatGPT 一致)
//   Shift+Enter   → 换行        (浏览器默认, 不拦)
//   ⌘/Ctrl+Enter  → 也发送      (兼容老习惯)
//
// 主按钮三态:
//   - 有输入 (idle)     → accent 底 + ArrowUp, 点击 submit
//   - 无输入 (idle)     → 灰底 + ArrowUp, disabled
//   - sending + onStop  → paper 反相底 + Square, 点击 onStop (中断当前 turn)
//
// 附件 + 号还没接后端 multipart 管线, 点了出 toast 占位.

import {
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from "react";
import { ArrowUp, Paperclip, Square } from "lucide-react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";

interface Props {
  onSend: (prompt: string) => void;
  // 父提供时 + disabled=true → 主按钮变 stop, 点击中断当前 turn.
  // 父没提供时 disabled 走旧逻辑 (灰按钮 disabled).
  onStop?: () => void;
  disabled?: boolean;
  placeholder?: string;
}

export function PromptInput({ onSend, onStop, disabled, placeholder }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const t = value.trim();
    if (!t || disabled) return;
    onSend(t);
    setValue("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Shift+Enter 不拦, 走浏览器默认换行; 中文输入法 composing 期间也不拦 (避免选词触发发送)
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  const canSend = !disabled && value.trim().length > 0;
  const showStop = !!disabled && !!onStop;

  return (
    <div className="border-t border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-6 py-4">
      <div className="mx-auto max-w-[820px]">
        <div
          onClick={(e) => {
            if ((e.target as HTMLElement).closest("button, textarea")) return;
            textareaRef.current?.focus();
          }}
          className={cn(
            "cursor-text rounded-[16px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] shadow-[0_1px_2px_rgba(20,30,50,0.03)] transition-all hover:shadow-[0_4px_16px_rgba(20,30,50,0.06)] focus-within:border-[color:var(--color-accent)] focus-within:shadow-[0_0_0_3px_rgba(61,90,128,0.12)]",
            disabled && "opacity-70",
          )}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e: ChangeEvent<HTMLTextAreaElement>) =>
              setValue(e.target.value)
            }
            onKeyDown={onKeyDown}
            disabled={disabled}
            placeholder={placeholder ?? "Continue the thread…"}
            rows={2}
            className="block w-full resize-none rounded-t-[16px] bg-transparent px-5 pt-5 pb-3 text-[15px] leading-relaxed text-[color:var(--color-paper)] placeholder:font-display placeholder:italic placeholder:text-[color:var(--color-ink-dim)] focus:outline-none disabled:cursor-not-allowed"
          />
          <div className="flex items-center justify-between gap-3 border-t border-[color:var(--color-line-soft)] px-3 py-2.5">
            <button
              type="button"
              onClick={() =>
                toast.info("Attachments coming soon — backend pipeline not wired yet")
              }
              title="Attach files (coming soon)"
              className="flex h-10 w-10 items-center justify-center rounded-[8px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]"
            >
              <Paperclip size={17} />
            </button>
            <button
              type="button"
              onClick={showStop ? onStop : submit}
              disabled={!showStop && !canSend}
              title={showStop ? "Stop generating" : "Send (Enter)"}
              className={cn(
                "flex h-10 w-10 items-center justify-center rounded-[8px] transition-colors",
                showStop
                  ? "bg-[color:var(--color-paper)] text-[color:var(--color-bg-card)] hover:opacity-85"
                  : canSend
                    ? "bg-[color:var(--color-accent)] text-white hover:opacity-90"
                    : "cursor-not-allowed bg-[color:var(--color-bg-raised)] text-[color:var(--color-ink)]",
              )}
            >
              {showStop ? (
                <Square size={13} className="fill-current" />
              ) : (
                <ArrowUp size={17} />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
