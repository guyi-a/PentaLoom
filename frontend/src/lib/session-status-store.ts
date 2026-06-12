// 会话级状态全局 store — 给 sidebar 实时显示 spinner / 等审批 / idle 用.
//
// 数据流: 后端 /sessions/status/stream SSE 长连推 status event → 这里 set 进
// Map<sid, status> → SessionList 用 useSessionStatus(sid) 订阅每条对应状态.
//
// 跟 SWR("sessions") 解耦: 列表 metadata (title / last_active_at) 仍走 SWR
// 冷拉; status 走这条独立 SSE 通道. 设计参考 docs/sidebar-realtime-plan.md.
//
// EventSource 的 lifecycle:
//   - openStatusStream(): App 挂载时调一次, 全局共享一份 SSE 长连
//   - closeStatusStream(): 卸载时调
//   - 浏览器 EventSource 断线自动重连 (默认行为), 不需要手撸; 重连时后端
//     subscribe 协议会先 dump 当前 _registry snapshot, 自然恢复.

import { create } from "zustand";

export type SessionStatus = "running" | "idle" | "waiting_approval";

interface SessionStatusEvent {
  type: "status";
  sid: string;
  status: SessionStatus;
}

interface State {
  // sid → status. idle 状态不进 Map (省 entries), 默认 idle.
  statuses: Map<string, SessionStatus>;
  // 当前 EventSource 实例 — 防多重 open. 单例.
  _es: EventSource | null;
  setStatus: (sid: string, status: SessionStatus) => void;
  openStream: () => void;
  closeStream: () => void;
}

export const useSessionStatusStore = create<State>((set, get) => ({
  statuses: new Map(),
  _es: null,

  setStatus: (sid, status) => {
    set((state) => {
      const next = new Map(state.statuses);
      if (status === "idle") {
        next.delete(sid);
      } else {
        next.set(sid, status);
      }
      return { statuses: next };
    });
  },

  openStream: () => {
    if (get()._es) return;  // 已经开了, idempotent
    const es = new EventSource("/api/sessions/status/stream");
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as SessionStatusEvent;
        if (ev.type === "status") {
          get().setStatus(ev.sid, ev.status);
        }
      } catch (err) {
        console.warn("session-status: bad SSE frame", e.data, err);
      }
    };
    es.onerror = () => {
      // 浏览器自动重连; 这里不主动 close, 让 EventSource 自己处理.
      // 后端短暂不可用时会刷一片 "EventSource error" 到 console, 接受.
    };
    set({ _es: es });
  },

  closeStream: () => {
    const es = get()._es;
    if (es) {
      es.close();
      set({ _es: null });
    }
  },
}));

// 选择器: 给 SessionList 单条订阅用. sid 不在 Map 中默认 "idle".
export function useSessionStatus(sid: string): SessionStatus {
  return useSessionStatusStore(
    (s) => s.statuses.get(sid) ?? "idle",
  );
}
