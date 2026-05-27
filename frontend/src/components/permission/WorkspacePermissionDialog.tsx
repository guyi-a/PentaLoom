// Stage 2 dynamic workspace mounting 的前端弹窗.
//
// 当 SSE 流里出现 mcp__pentaloom__request_workspace_dir 的 tool_use 帧, ChatStream 会
// 提取 (tool_use_id, path, reason) 实例化本组件. 用户必须做选择, 才会消失.
// 选择后 POST /chat/permission/:tool_use_id, 后端 resolve future, agent 继续推进,
// 自然返回 tool_result 帧, ChatStream 检测到 → 弹窗消失.
//
// 这是模态: 不让用户点外面消失, 不让按 ESC 取消, 必须做选择.

import { useState } from "react";
import { toast } from "sonner";
import { useSWRConfig } from "swr";
import { AlertTriangle, Folder } from "lucide-react";

import { api } from "@/lib/api";
import { cn, shortenPath } from "@/lib/utils";

interface Props {
  sessionId: string;
  toolUseId: string;
  path: string;
  reason: string;
}

export function WorkspacePermissionDialog({
  sessionId,
  toolUseId,
  path,
  reason,
}: Props) {
  const { mutate } = useSWRConfig();
  const [busy, setBusy] = useState<"allow" | "deny" | null>(null);

  async function decide(allow: boolean) {
    if (busy) return;
    setBusy(allow ? "allow" : "deny");
    try {
      // workspace 一次性, allow_once 就够; 后端在 allow_session 上也是等价处理.
      await api.respondPermission(toolUseId, {
        session_id: sessionId,
        decision: allow ? "allow_once" : "deny",
      });
      toast.success(
        allow ? `Mounted ${shortenPath(path, 40)}` : "Mount denied",
      );
      if (allow) mutate("sessions");
    } catch (err) {
      toast.error(`Response failed: ${String(err)}`);
      setBusy(null);
    }
    // 不主动收弹窗 — 等 ChatStream 检测到 tool_result 自然消失
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-[min(520px,90vw)] overflow-hidden rounded-[8px] border border-[color:var(--color-line-strong)] bg-[color:var(--color-bg-card)] shadow-[0_20px_60px_-15px_rgba(20,30,50,0.18)]">
        {/* 顶 */}
        <div className="flex items-center gap-2.5 border-b border-[color:var(--color-line)] px-5 py-3">
          <AlertTriangle
            size={14}
            className="text-[color:var(--color-accent)]"
          />
          <div className="text-[13px] font-medium text-[color:var(--color-paper)]">
            Workspace mount request
          </div>
        </div>

        {/* 体 */}
        <div className="space-y-4 px-5 py-5">
          <div>
            <div className="mb-1.5 text-[11px] font-medium text-[color:var(--color-ink)]">
              Path
            </div>
            <div className="flex items-center gap-2 rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] px-3 py-2 font-mono text-[12px] text-[color:var(--color-paper)]">
              <Folder size={12} className="text-[color:var(--color-thread-file)]" />
              <span className="truncate" title={path}>
                {path}
              </span>
            </div>
          </div>

          <div>
            <div className="mb-1.5 text-[11px] font-medium text-[color:var(--color-ink)]">
              Reason
            </div>
            <div className="rounded-[5px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] px-3 py-2 text-[13px] leading-relaxed text-[color:var(--color-paper-dim)]">
              {reason || (
                <span className="text-[11px] text-[color:var(--color-ink)]">
                  (no reason provided)
                </span>
              )}
            </div>
          </div>

          <p className="text-[11px] leading-relaxed text-[color:var(--color-ink)]">
            Allowing will add this directory to the session workspace. The assistant
            can read &amp; write its contents on subsequent turns. Cannot be undone in
            this session.
          </p>
        </div>

        {/* 底 */}
        <div className="flex gap-2 border-t border-[color:var(--color-line)] bg-[color:var(--color-bg-soft)] px-4 py-3">
          <button
            type="button"
            onClick={() => decide(false)}
            disabled={busy !== null}
            className={cn(
              "flex-1 rounded-[5px] border px-3 py-2 text-[12px] font-medium transition-colors",
              busy === "deny"
                ? "border-[color:var(--color-error)] bg-[color:var(--color-error)]/10 text-[color:var(--color-error)]"
                : "border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] text-[color:var(--color-paper-dim)] hover:border-[color:var(--color-error)] hover:text-[color:var(--color-error)]",
              busy && busy !== "deny" && "cursor-not-allowed opacity-50",
            )}
          >
            {busy === "deny" ? "Sending…" : "Deny"}
          </button>
          <button
            type="button"
            onClick={() => decide(true)}
            disabled={busy !== null}
            className={cn(
              "flex-1 rounded-[5px] px-3 py-2 text-[12px] font-medium transition-colors",
              busy === "allow"
                ? "bg-[color:var(--color-accent)]/15 text-[color:var(--color-accent)]"
                : "bg-[color:var(--color-accent)] text-white hover:opacity-90",
              busy && busy !== "allow" && "cursor-not-allowed opacity-50",
            )}
          >
            {busy === "allow" ? "Connecting…" : "Allow"}
          </button>
        </div>
      </div>
    </div>
  );
}
