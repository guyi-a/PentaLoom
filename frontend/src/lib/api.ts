// HTTP + SSE helpers. 后端在 vite proxy /api → 127.0.0.1:8090.

import type {
  AddEmailAccountBody,
  AppDetailResponse,
  AppWatchFilesResponse,
  ApprovalMode,
  BrowseResponse,
  ConnectionStatus,
  EmailAccountListResponse,
  EmailMutationResult,
  EmailProviderListResponse,
  EmailTestResult,
  FilePreviewMeta,
  Frame,
  FsTreeNode,
  HistoryMessage,
  OpenFileResp,
  PatchMountsBody,
  PermissionDecisionBody,
  PermissionDecisionResp,
  SessionMeta,
  SettingsResponse,
  TextPreviewResult,
  WeaverProductsResponse,
  WorkflowDetailResponse,
  XlsxWorkbookPreview,
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
  // 右栏 WorkspaceTree 拉某个 mount/sandbox 子树. 默认 max_depth=8.
  getFsTree: (sessionId: string, path: string, maxDepth = 8) =>
    http<FsTreeNode>(
      `/fs/tree?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}&max_depth=${maxDepth}`,
    ),
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
  // files (含 absolute_path 给 openFile 用) + meta + recent runs + running services.
  getAppDetail: (name: string) =>
    http<AppDetailResponse>(`/weaver/apps/${encodeURIComponent(name)}/detail`),
  // M17 dynamic workflow detail — WorkflowDetailModal 用. 含 definition + meta + 最近 20 条 run.
  getWorkflowDetail: (name: string) =>
    http<WorkflowDetailResponse>(`/weaver/workflows/${encodeURIComponent(name)}/detail`),
  // 单个 watch component 暴露目录的文件清单 — Phase E lazy fetch (用户在 modal 展开
  // 某个 watch 才拉, 不在 getAppDetail 里 inline 拉避免大目录拖慢)
  listWatchFiles: (appName: string, watchName: string) =>
    http<AppWatchFilesResponse>(
      `/weaver/apps/${encodeURIComponent(appName)}/watches/${encodeURIComponent(watchName)}/files`,
    ),
  // D-4 follow-up: 手动停一个 running service. 下次 invoke_app 自动重 spawn,
  // 所以不会"停了再也启不起来" — stop 只是释放进程 + 端口.
  stopAppService: (appName: string, serviceName: string) =>
    http<{ name: string; service: string; stopped: boolean }>(
      `/weaver/apps/${encodeURIComponent(appName)}/services/${encodeURIComponent(serviceName)}/stop`,
      { method: "POST" },
    ),
  // 中断当前 turn — 两步杀: SDK interrupt + 本地 stream task cancel.
  // Idempotent, 重复调返 204; 跑完已经无 buffer 也返 204 不报错.
  stopChat: (sid: string) =>
    http<void>(`/chat/${sid}/stop`, { method: "POST" }),

  // 审批模式 — per-session 仅内存. session 还没 build (新对话还没发第一条) 时
  // GET 返默认 default; PATCH 返 404, 调用方应吞掉 (用户的偏好留前端 store,
  // 第一次 send 之后再同步一次).
  getApprovalMode: (sid: string) =>
    http<{ mode: ApprovalMode }>(`/chat/${sid}/approval-mode`),
  setApprovalMode: (sid: string, mode: ApprovalMode) =>
    http<{ mode: ApprovalMode }>(`/chat/${sid}/approval-mode`, {
      method: "PATCH",
      json: { mode },
    }),

  // M19 file preview — sandbox/mount 鉴权同 fs/open.
  getPreviewMeta: (sessionId: string, path: string) =>
    http<FilePreviewMeta>(
      `/fs/preview/stat?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`,
    ),
  getPreviewText: (sessionId: string, path: string, maxBytes?: number) => {
    const max = maxBytes ? `&max_bytes=${maxBytes}` : "";
    return http<TextPreviewResult>(
      `/fs/preview/text?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}${max}`,
    );
  },
  // xlsx 结构化预览 (openpyxl 解析). docx/pptx 不走这, 它们 fetch ArrayBuffer 给前端渲染库.
  getPreviewXlsx: (sessionId: string, path: string) =>
    http<XlsxWorkbookPreview>(
      `/fs/preview/office/xlsx?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`,
    ),
  // 直接拼 URL — 给 <img|iframe|video src=> 用, 不走 fetch + JSON 解析.
  // 跟 /fs/preview/file 同一份 endpoint, 鉴权走 query string.
  getPreviewFileUrl: (sessionId: string, path: string): string =>
    `${BASE}/fs/preview/file?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`,

  // ──── Settings ──────────────────────────────────────────────
  getSettings: () => http<SettingsResponse>("/settings"),
  getConnections: () => http<ConnectionStatus>("/settings/connections"),
  patchSettings: (body: { theme: string }) =>
    http<SettingsResponse>("/settings", { method: "PATCH", json: body }),

  // ──── Email ────────────────────────────────────────────────
  getEmailProviders: () => http<EmailProviderListResponse>("/email/providers"),
  getEmailAccounts: () => http<EmailAccountListResponse>("/email/accounts"),
  addEmailAccount: (body: AddEmailAccountBody) =>
    http<EmailMutationResult>("/email/accounts", { method: "POST", json: body }),
  deleteEmailAccount: (id: string) =>
    http<EmailMutationResult>(`/email/accounts/${id}`, { method: "DELETE" }),
  testEmailAccount: (id: string) =>
    http<EmailTestResult>(`/email/accounts/${id}/test`, { method: "POST" }),
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

  return finalizeChatStream(res, controller, "chat");
}

// multipart 版 chatStream — composer 有 draft files / 粘贴图片时走这条. 后端
// /chat/with-attachments 同时处理两条通路:
//   - files     → 落盘 sandbox/attachments/{name} (同名 (N) 避让), 走 internal block 引用
//   - inlineImages → 不落盘, 转 base64 + Anthropic image content block 直接喂 SDK
// 跟 chatStream 的差异:
//   - body 用 FormData 不是 JSON
//   - mounted_dirs 走 JSON.stringify 编码 (multipart 字段限制)
// 共性: 响应 + abort 语义跟 chatStream 一模一样, 复用 finalizeChatStream.
export async function chatStreamWithAttachments(args: {
  prompt: string;
  sessionId?: string;
  mountedDirs?: string[];
  files: File[];
  inlineImages?: File[];
  signal?: AbortSignal;
}): Promise<ChatStreamHandle> {
  const controller = new AbortController();
  const signal = args.signal
    ? mergeSignals(controller.signal, args.signal)
    : controller.signal;

  const form = new FormData();
  form.append("prompt", args.prompt);
  if (args.sessionId) form.append("session_id", args.sessionId);
  if (args.mountedDirs && args.mountedDirs.length > 0) {
    form.append("mounted_dirs", JSON.stringify(args.mountedDirs));
  }
  for (const f of args.files) {
    // 用原文件名 — 后端 sanitize_filename 会处理不安全字符 + 限长
    form.append("files", f, f.name);
  }
  for (const img of args.inlineImages ?? []) {
    // 粘贴的图通常 type 已设 (浏览器 clipboard item 自带 mime); name 由 PromptInput 兜底.
    form.append("inline_images", img, img.name);
  }

  const res = await fetch(`${BASE}/chat/with-attachments`, {
    method: "POST",
    body: form,
    signal,
  });

  return finalizeChatStream(res, controller, "chat-with-attachments");
}

async function finalizeChatStream(
  res: Response,
  controller: AbortController,
  label: string,
): Promise<ChatStreamHandle> {
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`${label} ${res.status} ${res.statusText} ${text}`);
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
