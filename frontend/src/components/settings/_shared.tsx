// 设置页的 UI 模式: section header + row.
import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

export function SectionHeader({
  label,
  actions,
}: {
  label: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-end gap-2 border-b border-[color:var(--color-line)] pb-2">
      <h2 className="min-w-0 flex-1 font-display text-[14px] font-medium tracking-[-0.006em] text-[color:var(--color-paper)]">
        {label}
      </h2>
      {actions}
    </div>
  );
}

export function SettingRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-start gap-4 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium text-[color:var(--color-paper)]">
          {label}
        </div>
        {hint && (
          <div className="mt-0.5 text-[11.5px] text-[color:var(--color-ink)]">
            {hint}
          </div>
        )}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export function SettingInput({
  value,
  onChange,
  placeholder,
  type = "text",
  monospace = false,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: "text" | "password";
  monospace?: boolean;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={cn(
        "w-[260px] rounded-[6px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] px-3 py-1.5 text-[12.5px] text-[color:var(--color-paper)] placeholder:text-[color:var(--color-ink-dim)] focus:border-[color:var(--color-accent)] focus:outline-none",
        monospace && "font-mono",
      )}
    />
  );
}

export function SettingSelect({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-[260px] rounded-[6px] border border-[color:var(--color-line)] bg-[color:var(--color-bg-deep)] px-3 py-1.5 text-[12.5px] text-[color:var(--color-paper)] focus:border-[color:var(--color-accent)] focus:outline-none"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
