// 审批模式 store — keyed by sid, per-conversation 仅内存.
//
// 不 persist (localStorage): 用户决策每个会话从 default 起步, 重启 / 切对话
// 重新选. ChatPage 切对话 tab 时 picker 会读 store 里这个 sid 对应的 mode,
// 没有就 fall back default.
//
// 后端是真理之源 (LoomPool._Entry.approval_mode_ref). 前端 store 是缓存 +
// "session 还没 build 时暂存用户偏好": 用户在新对话里切了 picker 还没发消息,
// 此时 PATCH /chat/{sid}/approval-mode 会 404, 我们仍把选择记在 store, 第一次
// send 之后 ChatPage 会调一次 setApprovalMode 兜底同步.

import { create } from "zustand";

import type { ApprovalMode } from "./types";

interface State {
  modeBySession: Record<string, ApprovalMode>;
  // 给 EmptyPage 用: 用户还没发第一条消息时切的 picker 暂存这里. 一旦
  // EmptyPage 拿到新 sid, 调 commitPendingTo(sid) 把 pendingMode 移到该 sid
  // 上 + reset pendingMode 回 "default".
  pendingMode: ApprovalMode;
  // 读: sid 不存在或没设过返 "default".
  getMode: (sid: string) => ApprovalMode;
  // 写: 仅更新 store, 不调后端. 调用方 (picker) 自己 PATCH.
  setMode: (sid: string, mode: ApprovalMode) => void;
  // 写 pendingMode (没 sid 时 picker 用).
  setPendingMode: (mode: ApprovalMode) => void;
  // EmptyPage 拿到 sid 后调一次 — 移交 pendingMode 给 sid + reset pendingMode.
  // 返回移交的 mode (调用方拿来 PATCH 后端).
  commitPendingTo: (sid: string) => ApprovalMode;
  // session 删除 / evict 时清理.
  clearSession: (sid: string) => void;
}

export const useApprovalModeStore = create<State>((set, get) => ({
  modeBySession: {},
  pendingMode: "default",
  getMode: (sid) => get().modeBySession[sid] ?? "default",
  setMode: (sid, mode) =>
    set((s) => ({
      modeBySession: { ...s.modeBySession, [sid]: mode },
    })),
  setPendingMode: (mode) => set({ pendingMode: mode }),
  commitPendingTo: (sid) => {
    const pending = get().pendingMode;
    set((s) => ({
      modeBySession: { ...s.modeBySession, [sid]: pending },
      pendingMode: "default",
    }));
    return pending;
  },
  clearSession: (sid) =>
    set((s) => {
      if (!(sid in s.modeBySession)) return s;
      const next = { ...s.modeBySession };
      delete next[sid];
      return { modeBySession: next };
    }),
}));
