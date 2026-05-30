import clsx from "clsx";
import { twMerge } from "tailwind-merge";
import type { ClassValue } from "clsx";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  const now = Date.now();
  const sec = Math.max(0, Math.floor((now - t) / 1000));
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startOfYesterday = new Date(startOfToday.getTime() - 86400000);
  if (t >= startOfYesterday.getTime() && t < startOfToday.getTime()) {
    return "yesterday";
  }
  const day = Math.floor((now - t) / 86400000);
  if (day < 7) return `${day}d`;
  // 一年内显示 "Mar 14"; 跨年加年份
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
