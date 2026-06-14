// 审批模式 picker — 挂在 PromptInput 工具栏 (Paperclip 旁边).
//
// 三档:
//   Default      — Shield (灰)        每次都问
//   Auto         — ShieldCheck (主色) 无害静默, destructive 仍审, 其他 LLM 兜底
//   Full access  — ShieldAlert (warn) 全自动批, 仅 destructive 审
//
// sessionId === null (新对话还没发第一条消息) 时仍可切, 只更新 store; 不 PATCH
// 后端. ChatPage 在 send 之后会兜底调一次 setApprovalMode.

import { Check, Shield, ShieldAlert, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useApprovalModeStore } from "@/lib/approval-mode-store";
import type { ApprovalMode } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ModeMeta {
  id: ApprovalMode;
  icon: typeof Shield;
  label: string;
  desc: string;
}

const MODES: readonly ModeMeta[] = [
  {
    id: "default",
    icon: Shield,
    label: "Default",
    desc: "每个工具调用都弹审批",
  },
  {
    id: "auto",
    icon: ShieldCheck,
    label: "Auto",
    desc: "无害命令静默放行, 破坏性操作仍审, 其他走 LLM 兜底",
  },
  {
    id: "full_access",
    icon: ShieldAlert,
    label: "Full access",
    desc: "全自动批, 仅破坏性操作 (rm / kill / git push --force) 弹审",
  },
];

interface Props {
  // null = EmptyPage 还没创建 session. picker 切的 mode 写到 store.pendingMode,
  // 第一次 send 拿到 sid 后由 EmptyPage 调 commitPendingTo(sid) 移交.
  sessionId: string | null;
  disabled?: boolean;
}

export function ApprovalModePicker({ sessionId, disabled }: Props) {
  const mode = useApprovalModeStore((s) =>
    sessionId ? s.modeBySession[sessionId] ?? "default" : s.pendingMode,
  );
  const setMode = useApprovalModeStore((s) => s.setMode);
  const setPendingMode = useApprovalModeStore((s) => s.setPendingMode);

  const current = MODES.find((m) => m.id === mode) ?? MODES[0];
  const ActiveIcon = current.icon;

  async function pick(next: ApprovalMode) {
    if (next === mode) return;
    const label = MODES.find((m) => m.id === next)?.label ?? next;
    if (sessionId) {
      setMode(sessionId, next);
      try {
        await api.setApprovalMode(sessionId, next);
        toast.success(`审批模式: ${label}`);
      } catch {
        // 404 = session 还没 build (theoretically 不该, ChatPage 进来时一般已有).
        // store 已记下, ChatPage 在第一次 send 之后会重 PATCH 兜底.
        toast.success(`审批模式: ${label} (待会话激活后生效)`);
      }
    } else {
      // EmptyPage 没 sid — 写 pendingMode, 不调后端. EmptyPage send 拿到 sid
      // 后调 commitPendingTo(sid) 移交.
      setPendingMode(next);
      toast.success(`审批模式: ${label} (新会话生效)`);
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          title={`审批模式: ${current.label} — ${current.desc}`}
          className={cn(
            "flex h-10 items-center gap-1.5 rounded-[8px] px-2.5 text-[13px] text-[color:var(--color-paper-dim)] transition-colors hover:bg-[color:var(--color-bg-raised)] hover:text-[color:var(--color-paper)]",
            "disabled:cursor-not-allowed disabled:opacity-50",
            mode === "full_access" &&
              "text-[color:var(--color-warn)] hover:text-[color:var(--color-warn)]",
            mode === "auto" &&
              "text-[color:var(--color-accent)] hover:text-[color:var(--color-accent)]",
          )}
        >
          <ActiveIcon size={15} />
          <span>{current.label}</span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-[300px]"
        onCloseAutoFocus={(e) => e.preventDefault()}
      >
        {MODES.map((m) => {
          const Icon = m.icon;
          const active = m.id === mode;
          return (
            <DropdownMenuItem
              key={m.id}
              onSelect={() => pick(m.id)}
              className="flex items-start gap-2 py-2"
            >
              <Icon
                size={15}
                className="mt-[3px] shrink-0 text-[color:var(--color-paper-dim)]"
              />
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[13px] font-medium">{m.label}</span>
                  {active && (
                    <Check
                      size={12}
                      className="text-[color:var(--color-accent)]"
                    />
                  )}
                </div>
                <div className="mt-0.5 text-[11px] leading-snug text-[color:var(--color-paper-dim)]">
                  {m.desc}
                </div>
              </div>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
