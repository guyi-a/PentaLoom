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
  if (sec < 60) return `${sec}s 前`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m 前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h 前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d 前`;
  return new Date(iso).toLocaleDateString("zh-CN");
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
