// Radix HoverCard 薄封装 — 用于 composer image chip 的 hover 大图预览.
// 主题 className 跟 PentaLoom Nordic Light 对齐 (--color-bg-card / --color-line),
// 不照搬 shadcn 的 bg-popover 黑暗模式.
//
// 用法:
//   <HoverCard openDelay={150} closeDelay={50}>
//     <HoverCardTrigger asChild><div ... /></HoverCardTrigger>
//     <HoverCardContent>...</HoverCardContent>
//   </HoverCard>

import * as HoverCardPrimitive from "@radix-ui/react-hover-card";
import { type ComponentProps } from "react";

import { cn } from "@/lib/utils";

export const HoverCard = HoverCardPrimitive.Root;
export const HoverCardTrigger = HoverCardPrimitive.Trigger;

export function HoverCardContent({
  className,
  align = "start",
  sideOffset = 6,
  ...props
}: ComponentProps<typeof HoverCardPrimitive.Content>) {
  return (
    <HoverCardPrimitive.Portal>
      <HoverCardPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 rounded-[10px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-card)] p-2 shadow-[0_8px_24px_rgba(20,30,50,0.12)]",
          "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
          className,
        )}
        {...props}
      />
    </HoverCardPrimitive.Portal>
  );
}
