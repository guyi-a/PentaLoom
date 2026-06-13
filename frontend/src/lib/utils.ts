import clsx from "clsx";
import { twMerge } from "tailwind-merge";
import type { ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Sidebar 单条时间戳 — 紧凑相对格式, 单位 ≤ 4 字符 防撞 title.
// 全部走 "N + 单位" 统一格式: 不区分 "yesterday" / "just now" 这种长字符串,
// 视觉跟 1d / 5h / 23m 整齐对齐. 超 4 周走绝对日期.
//   < 1m  → "now"        // 跟 "23m" 长度对齐, 不再 "just now" 撞 title
//   < 1h  → "Nm"         // 23m
//   < 1d  → "Nh"         // 5h (含原 "yesterday": 24h 内仍是 23h 之类)
//   < 7d  → "Nd"         // 3d (原 "yesterday" 跨天后变 "1d", 4 字符内)
//   < 4w  → "Nw"         // 2w
//   ≥ 4w  → "Mar 14" / "Mar 14, 2025"
export function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  const diffMs = Math.max(0, Date.now() - t);
  const MIN = 60 * 1000;
  const HOUR = 60 * MIN;
  const DAY = 24 * HOUR;
  const WEEK = 7 * DAY;

  if (diffMs < MIN) return "now";
  if (diffMs < HOUR) return `${Math.floor(diffMs / MIN)}m`;
  if (diffMs < DAY) return `${Math.floor(diffMs / HOUR)}h`;
  if (diffMs < WEEK) return `${Math.floor(diffMs / DAY)}d`;
  if (diffMs < 4 * WEEK) return `${Math.floor(diffMs / WEEK)}w`;

  // > 4 weeks: 一年内 "Mar 14", 跨年加年份
  const d = new Date(iso);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
  });
}

export type TimeGroup = "Today" | "Yesterday" | "Last 7 days" | "Earlier";
export const TIME_GROUP_ORDER: TimeGroup[] = ["Today", "Yesterday", "Last 7 days", "Earlier"];

export function getTimeGroup(iso: string): TimeGroup {
  const t = new Date(iso).getTime();
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startOfYesterday = new Date(startOfToday.getTime() - 86400000);
  const startOf7Days = new Date(startOfToday.getTime() - 6 * 86400000);
  if (t >= startOfToday.getTime()) return "Today";
  if (t >= startOfYesterday.getTime()) return "Yesterday";
  if (t >= startOf7Days.getTime()) return "Last 7 days";
  return "Earlier";
}

export function shortenPath(p: string, max = 32): string {
  if (p.length <= max) return p;
  const parts = p.split("/").filter(Boolean);
  if (parts.length <= 2) return "…" + p.slice(-(max - 1));
  return `${parts[0]}/…/${parts[parts.length - 1]}`;
}

export function shortenSid(sid: string): string {
  if (!sid) return "";
  return sid.slice(0, 8);
}
