// Radix AlertDialog 薄封装 — 用于"破坏性操作的二次确认", 比如删 session.
//
// 跟 Dialog 的差别 (语义层面):
//   - AlertDialog 默认有 role="alertdialog", 强制焦点 + 不允许点 backdrop 关
//     (用户必须显式按 Cancel/Action 之一)
//   - 适合"删除 / 重置 / 清空"等不可恢复操作
//
// 主题对齐 PentaLoom Nordic Light, 跟 dialog.tsx 同档 className 风格.

import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";
import { type ComponentProps } from "react";

import { cn } from "@/lib/utils";

export const AlertDialog = AlertDialogPrimitive.Root;
export const AlertDialogTrigger = AlertDialogPrimitive.Trigger;
export const AlertDialogCancel = AlertDialogPrimitive.Cancel;
export const AlertDialogAction = AlertDialogPrimitive.Action;

export function AlertDialogContent({
  className,
  children,
  ...props
}: ComponentProps<typeof AlertDialogPrimitive.Content>) {
  return (
    <AlertDialogPrimitive.Portal>
      <AlertDialogPrimitive.Overlay
        className={cn(
          "fixed inset-0 z-50 bg-black/50",
          "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0",
        )}
      />
      <AlertDialogPrimitive.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-[min(420px,90vw)] -translate-x-1/2 -translate-y-1/2 rounded-[12px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] p-5 shadow-[0_20px_60px_rgba(20,30,50,0.18)] outline-none",
          "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95",
          className,
        )}
        {...props}
      >
        {children}
      </AlertDialogPrimitive.Content>
    </AlertDialogPrimitive.Portal>
  );
}

export function AlertDialogTitle({
  className,
  ...props
}: ComponentProps<typeof AlertDialogPrimitive.Title>) {
  return (
    <AlertDialogPrimitive.Title
      className={cn(
        "font-display text-[16px] font-medium tracking-[-0.005em] text-[color:var(--color-paper)]",
        className,
      )}
      {...props}
    />
  );
}

export function AlertDialogDescription({
  className,
  ...props
}: ComponentProps<typeof AlertDialogPrimitive.Description>) {
  return (
    <AlertDialogPrimitive.Description
      className={cn(
        "mt-2 text-[13px] leading-relaxed text-[color:var(--color-paper-dim)]",
        className,
      )}
      {...props}
    />
  );
}
