// Radix Dialog 薄封装 — 仅导出 lightbox 真正用到的 5 个原语 (Root / Trigger /
// Portal / Overlay / Content). Title/Description/Header/Footer 等 form-style
// 配套件没用到, 不抄, 后续真要用再补.
//
// 主题 className 走 PentaLoom Nordic Light tokens, 不照搬 shadcn 默认.

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { type ComponentProps } from "react";

import { cn } from "@/lib/utils";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  children,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay
        className={cn(
          "fixed inset-0 z-50 bg-black/60 backdrop-blur-sm",
          "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0",
        )}
      />
      <DialogPrimitive.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 outline-none",
          "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95",
          className,
        )}
        {...props}
      >
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
