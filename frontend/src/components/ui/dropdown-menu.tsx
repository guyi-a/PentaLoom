// Radix DropdownMenu 薄封装 — 用于 sidebar 行的 ⋯ 上下文菜单等场景.
// 仅导出当前真正用到的 5 个原语 (Root / Trigger / Content / Item / Separator),
// SubMenu / Group / RadioGroup / CheckItem 真要用再补.
//
// Item variant: "default" / "destructive" — destructive 文字 + hover 背景走
// --color-error tint, 跟 lucide Trash2 同色调.

import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { type ComponentProps } from "react";

import { cn } from "@/lib/utils";

export const DropdownMenu = DropdownMenuPrimitive.Root;
export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;

export function DropdownMenuContent({
  className,
  sideOffset = 4,
  align = "end",
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        sideOffset={sideOffset}
        align={align}
        className={cn(
          "z-50 min-w-[160px] rounded-[8px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] p-1 text-[13px] shadow-[0_8px_24px_rgba(20,30,50,0.12)]",
          "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0 data-[state=open]:zoom-in-95 data-[state=closed]:zoom-out-95 data-[side=bottom]:slide-in-from-top-1 data-[side=top]:slide-in-from-bottom-1",
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

export function DropdownMenuItem({
  className,
  variant = "default",
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Item> & {
  variant?: "default" | "destructive";
}) {
  return (
    <DropdownMenuPrimitive.Item
      data-variant={variant}
      className={cn(
        "relative flex cursor-default select-none items-center gap-2 rounded-[5px] px-2 py-1.5 outline-none transition-colors",
        "text-[color:var(--color-paper)]",
        "data-[highlighted]:bg-[color:var(--color-bg-raised)]",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        "[&_svg]:size-3.5 [&_svg]:shrink-0 [&_svg:not([class*='text-'])]:text-[color:var(--color-paper-dim)]",
        // destructive: 红字 + hover 时浅红底 + svg 也跟着红
        "data-[variant=destructive]:text-[color:var(--color-error)]",
        "data-[variant=destructive]:data-[highlighted]:bg-[color:var(--color-error)]/10",
        "data-[variant=destructive]:[&_svg:not([class*='text-'])]:text-[color:var(--color-error)]/70",
        className,
      )}
      {...props}
    />
  );
}

export function DropdownMenuSeparator({
  className,
  ...props
}: ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return (
    <DropdownMenuPrimitive.Separator
      className={cn(
        "my-1 h-px bg-[color:var(--color-line-soft)]",
        className,
      )}
      {...props}
    />
  );
}
