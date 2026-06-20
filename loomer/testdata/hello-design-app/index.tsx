// hello-design-app — 验证 importmap 全套 specifier 跟 Tailwind CDN 都通.
//
// 跑法:
//   cd loomer && go build -o /tmp/loomer . && /tmp/loomer --entry testdata/hello-design-app/index.tsx
//
// 期望:
//   - 弹窗 720×480, 标题 "design demo"
//   - 暗色面板 (bg-[#08090a] page + bg-[#191a1b] card)
//   - lucide-react Sparkles icon 渲染
//   - radix-ui Switch 组件可点
//   - cn() 合并 className 不冲突
//   - arbitrary value (`bg-[#191a1b]` `tracking-[-0.022em]`) 真渲染
import { useState } from "react";
import { Sparkles, Check, X } from "lucide-react";
import { Switch } from "radix-ui";
import { cn } from "@/lib/utils";

export const windowConfig = {
  title: "design demo",
  width: 720,
  height: 480,
};

export default function App() {
  const [enabled, setEnabled] = useState(false);

  return (
    <div className="min-h-screen bg-[#08090a] text-[#f7f8f8] p-8 font-[system-ui]">
      <div className="max-w-xl mx-auto space-y-6">
        <header className="space-y-2">
          <div className="flex items-center gap-2">
            <Sparkles className="size-5 text-[#5e6ad2]" strokeWidth={2} />
            <h1 className="text-2xl font-semibold tracking-[-0.022em] leading-[1.05]">
              loomer design demo
            </h1>
          </div>
          <p className="text-sm text-[#8a8f98] leading-[1.5]">
            这个窗 import lucide-react / radix-ui / @/lib/utils, 全走 esm.sh
            解析. arbitrary value Tailwind class (bg-[#191a1b] / tracking-[...])
            由 cdn.tailwindcss.com 实时编译.
          </p>
        </header>

        <section
          className={cn(
            "rounded-lg border border-white/[0.08] bg-[#191a1b] p-5",
            "shadow-[0_4px_12px_rgba(0,0,0,0.25)]",
            "space-y-4",
          )}
        >
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[13px] font-medium text-[#d0d6e0]">
                Toggle (Radix Switch)
              </div>
              <div className="text-[12px] text-[#62666d] mt-0.5">
                radix-ui 聚合包 import 验证
              </div>
            </div>
            <Switch.Root
              checked={enabled}
              onCheckedChange={setEnabled}
              className={cn(
                "relative h-6 w-11 rounded-full transition-colors",
                "data-[state=checked]:bg-[#5e6ad2]",
                "data-[state=unchecked]:bg-[#28282c]",
                "focus:outline-none focus:ring-2 focus:ring-[#5e6ad2]/35",
              )}
            >
              <Switch.Thumb
                className={cn(
                  "block size-5 rounded-full bg-white shadow",
                  "transition-transform translate-x-0.5",
                  "data-[state=checked]:translate-x-[22px]",
                )}
              />
            </Switch.Root>
          </div>

          <div className="flex items-center gap-2 text-[12px] text-[#8a8f98]">
            {enabled ? (
              <>
                <Check className="size-4 text-[#22c55e]" />
                <span>enabled — cn() 合 data-[state=...] + transform</span>
              </>
            ) : (
              <>
                <X className="size-4 text-[#62666d]" />
                <span>disabled</span>
              </>
            )}
          </div>
        </section>

        <footer className="text-[11px] text-[#62666d] text-center pt-4">
          loomer · {new Date().getFullYear()}
        </footer>
      </div>
    </div>
  );
}
