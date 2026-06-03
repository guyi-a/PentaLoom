// HTTP + SSE helpers. 后端在 vite proxy /api → 127.0.0.1:8090.

import type {
  AppDetailResponse,
  BrowseResponse,
  Frame,
  HistoryMessage,
  OpenFileResp,
  PatchMountsBody,
  PermissionDecisionBody,
  PermissionDecisionResp,
  SessionMeta,
  WeaverProductsResponse,
} from "./types";

const BASE = "/api";

async function http<T>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const headers = new Headers(init?.headers);
  let body = init?.body;
  if (init?.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(`${BASE}${path}`, { ...init, headers, body });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText} ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ──── REST endpoints ─────────────────────────────────────────

export const api = {
  listSessions: () => http<SessionMeta[]>("/sessions"),
  getSession: (sid: string) => http<SessionMeta>(`/sessions/${sid}`),
  getMessages: (sid: string) =>
    http<HistoryMessage[]>(`/sessions/${sid}/messages`),
  patchSession: (sid: string, body: { title?: string | null }) =>
    http<SessionMeta>(`/sessions/${sid}`, { method: "PATCH", json: body }),
  deleteSession: (sid: string) =>
    http<{ session_id: string; deleted: Record<string, boolean> }>(
      `/sessions/${sid}`,
      { method: "DELETE" },
    ),
  respondPermission: (toolUseId: string, body: PermissionDecisionBody) =>
    http<PermissionDecisionResp>(`/chat/permission/${toolUseId}`, {
      method: "POST",
      json: body,
    }),
  browseDir: (path?: string) => {
    const q = path ? `?path=${encodeURIComponent(path)}` : "";
    return http<BrowseResponse>(`/fs/browse${q}`);
  },
  // 用系统默认 app 打开 path. reveal=true 时定位到文件 (Finder/Explorer 选中).
  // 后端按 session.mounted_dirs ∪ sandbox 子树校验, 越权返 403.
  openFile: (args: { sessionId: string; path: string; reveal?: boolean }) =>
    http<OpenFileResp>(`/fs/open`, {
      method: "POST",
      json: {
        session_id: args.sessionId,
        path: args.path,
        reveal: args.reveal ?? false,
      },
    }),
  // 改 session 的 mounted_dirs. 后端 evict LoomPool entry, 下条消息触发 client 重建.
  patchMounts: (sid: string, body: PatchMountsBody) =>
    http<SessionMeta>(`/sessions/${sid}/mounts`, { method: "PATCH", json: body }),
  // weaver 产物列表 (内置 + 用户织的). M14 只 skills 有数据.
  listWeaverProducts: () => http<WeaverProductsResponse>("/weaver/products"),
  // 单个 app 详情 — sidebar AppDetailPanel 用. 一次拉全: manifest summary +
  // files (含 absolute_path 给 openFile 用) + meta + recent runs.
  getAppDetail: (name: string) =>
    http<AppDetailResponse>(`/weaver/apps/${encodeURIComponent(name)}/detail`),
  // 中断当前 turn — 两步杀: SDK interrupt + 本地 stream task cancel.
  // Idempotent, 重复调返 204; 跑完已经无 buffer 也返 204 不报错.
  stopChat: (sid: string) =>
    http<void>(`/chat/${sid}/stop`, { method: "POST" }),
};

// ──── chat SSE ───────────────────────────────────────────────
// 用 fetch + ReadableStream 自己解析 `data: ` 行 (浏览器 EventSource 不能 POST).
// 调用方拿到 sid (从 response header) 和一个 async iterable of frames.

export interface ChatStreamHandle {
  sessionId: string;
  frames: AsyncIterable<Frame>;
  abort: () => void;
}

export async function chatStream(args: {
  prompt: string;
  sessionId?: string;
  mountedDirs?: string[];
  signal?: AbortSignal;
}): Promise<ChatStreamHandle> {
  const controller = new AbortController();
  const signal = args.signal
    ? mergeSignals(controller.signal, args.signal)
    : controller.signal;

  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: args.prompt,
      session_id: args.sessionId ?? null,
      mounted_dirs: args.mountedDirs ?? null,
    }),
    signal,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`chat ${res.status} ${res.statusText} ${text}`);
  }

  const sid =
    res.headers.get("x-session-id") ?? res.headers.get("X-Session-Id") ?? "";

  const frames = parseSSE(res.body);

  return {
    sessionId: sid,
    frames,
    abort: () => controller.abort(),
  };
}

// 重连当前 turn 的 SSE 流.
// 后端 stream_all 重放时把 delta 折叠成完整 text/thinking frame (streaming=true),
// 续订阅段才是 raw delta. 前端 reducer 按 (msg_uuid, index) 幂等 merge, ChatStream
// 按 history msg_uuid 跨源去重. 多次切走再回来 / 多 tab 都安全, 没有重复.
// 没活跃 buffer (没跑过 / 跑完已被覆盖) → 返 null.
export interface ResumeHandle {
  frames: AsyncIterable<Frame>;
  abort: () => void;
}

export async function resumeChat(args: {
  sessionId: string;
  signal?: AbortSignal;
}): Promise<ResumeHandle | null> {
  const controller = new AbortController();
  const signal = args.signal
    ? mergeSignals(controller.signal, args.signal)
    : controller.signal;

  const res = await fetch(`${BASE}/chat/${args.sessionId}/resume`, {
    method: "GET",
    signal,
  });

  if (res.status === 204) return null;
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`resume ${res.status} ${res.statusText} ${text}`);
  }

  const frames = parseSSE(res.body);
  return {
    frames,
    abort: () => controller.abort(),
  };
}

async function* parseSSE(stream: ReadableStream<Uint8Array>): AsyncIterable<Frame> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE event 分隔: 空行 (\n\n)
      let idx: number;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = chunk
          .split("\n")
          .filter((l) => l.startsWith("data: "))
          .map((l) => l.slice(6))
          .join("\n");
        if (!line) continue;
        try {
          yield JSON.parse(line) as Frame;
        } catch (e) {
          // 一帧坏的不该挂整流, log 一下继续
          console.warn("bad SSE frame", line, e);
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function mergeSignals(...signals: AbortSignal[]): AbortSignal {
  const ctrl = new AbortController();
  for (const s of signals) {
    if (s.aborted) {
      ctrl.abort();
      break;
    }
    s.addEventListener("abort", () => ctrl.abort(), { once: true });
  }
  return ctrl.signal;
}
