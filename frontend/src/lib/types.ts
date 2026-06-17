// PentaLoom 前后端数据契约.
// 跟 agent/pentaloom/routers/{chat,sessions}.py 的 pydantic / SSE frame 一一对应.
// 改一边记得改另一边.

// ──── REST ─────────────────────────────────────────────────────

export interface SessionMeta {
  session_id: string;
  title: string | null;
  mounted_dirs: string[];
  sandbox_dir: string;  // agent 默认 cwd, 后端从 settings 算出, 跟 mounted_dirs 互补
  created_at: string;
  last_active_at: string;
}

export interface TodoItem {
  seq: number;
  content: string;
  activeForm: string;
  status: "pending" | "in_progress" | "completed";
}

export interface HistoryMessage {
  role: "user" | "assistant";
  uuid: string;                       // SDK envelope uuid - 用作 React key
  message_id: string | null;          // anthropic message.id - 跨源去重 key (user 历史可能为 null)
  frames: Frame[];
  // 仅 user role + 后端剥掉 <pentaloom_internal_attachments> 块时挂. 老消息 / 无附件
  // 消息不会带这字段. 前端 ChatStream 在 user bubble text 空 + count > 0 时渲染
  // "📎 N 个文件" 占位 (no chips list — 那是 Phase 3).
  attachment_count?: number;
  // 内嵌图片 (粘贴的, 走 SDK content block, 不落盘) 的 src 列表 — src 是 data URL
  // (data:image/png;base64,...). 后端从 SDK transcript 的 image content block 直接
  // 转出, user bubble 用作 <img src> 渲缩略图 grid.
  // 量大的话 history payload 会膨胀, 真出问题再做 thumbnail 缓存 / lazy fetch.
  inline_images?: { src: string }[];
}

export interface PermissionDecisionBody {
  session_id: string;
  // allow_once: 仅这一次; allow_session: 加进本会话白名单 (仅 Bash / install_libs 有效);
  // deny: 拒绝. workspace / run_python_script 上 allow_session 退化为 allow_once.
  decision: "allow_once" | "allow_session" | "deny";
}

export interface PermissionDecisionResp {
  session_id: string;
  tool_use_id: string;
  decision: "allow_once" | "allow_session" | "deny";
  added_path: string | null;
  // 后端给 allow_session 工具算出来的免审 key. Bash 是命令串, install_libs 是
  // sorted(libs).join("\n"). 仅展示 / 排错用, 前端不参与判断.
  added_allowlist_key: string | null;
}

// ──── Approval mode (per-session 仅内存) ─────────────────────

// 三档审批策略, 对应后端 infra/approval/policy.py.
//   default      — 现状, 每个 HITL 工具调用都弹审批
//   auto         — Bash 无害命令静默放行, destructive 永远拦, 其他走 LLM 兜底
//   full_access  — 所有非 destructive 自动放行, destructive 仍要审
// 用户在 PromptInput 工具栏 picker 里切换. 每个会话独立, evict / 重启重置 default.
export type ApprovalMode = "default" | "auto" | "full_access";

export const APPROVAL_MODES: readonly ApprovalMode[] = [
  "default",
  "auto",
  "full_access",
] as const;

// ──── Settings ──────────────────────────────────────────────

export interface SettingsResponse {
  theme: string;
  version: string;
}

export interface BrowserSummary {
  browser_id: string;
  label: string;
}

export interface ConnectionStatus {
  browser_bridge_ready: boolean;
  browser_bridge_browsers: number;
  browser_bridge_detail: BrowserSummary[];
  email_connected: boolean;
  email_account: string | null;
}

// ──── Email ─────────────────────────────────────────────────

export interface EmailProviderInfo {
  id: string;
  display_name: string;
  email_suffix: string;
}

export interface EmailProviderListResponse {
  providers: EmailProviderInfo[];
}

export interface EmailAccountResponse {
  id: string;
  provider: string;
  email: string;
  display_name: string | null;
  is_default: boolean;
}

export interface EmailAccountListResponse {
  accounts: EmailAccountResponse[];
  default_account_id: string | null;
}

export interface EmailMutationResult {
  ok: boolean;
  message: string;
  error_code: string | null;
  account_id: string | null;
  email: string | null;
}

export interface EmailTestResult {
  ok: boolean;
  message: string;
  error_code: string | null;
}

export interface AddEmailAccountBody {
  provider: string;
  email: string;
  password: string;
  display_name?: string;
}

// ──── SSE frames (chat.py _serialize 的产出) ──────────────────

export type Frame =
  | TextFrame
  | TextDeltaFrame
  | ThinkingFrame
  | ThinkingDeltaFrame
  | ToolUseFrame
  | ToolResultFrame
  | TaskStartedFrame
  | TaskProgressFrame
  | TaskDoneFrame
  | ResultFrame
  | ErrorFrame
  | StreamEndFrame
  | UserPromptFrame
  | PermissionRequestFrame
  | PermissionResolvedFrame;

// 流式 turn 中, 后端按 token 推 text_delta / thinking_delta; 前端 reducer 把同
// (msg_uuid, index) 的 deltas 合并成一个 TextFrame / ThinkingFrame (带 streaming=true),
// 直到一轮 turn 结束. 历史里只会出现已合并好的完整 frame, 不会出现 *_delta.
export interface TextFrame {
  type: "text";
  text: string;
  msg_uuid?: string;
  index?: number;
  streaming?: boolean;
}
export interface TextDeltaFrame {
  type: "text_delta";
  msg_uuid: string;
  index: number;
  text: string;
}
export interface ThinkingFrame {
  type: "thinking";
  text: string;
  msg_uuid?: string;
  index?: number;
  streaming?: boolean;
}
export interface ThinkingDeltaFrame {
  type: "thinking_delta";
  msg_uuid: string;
  index: number;
  text: string;
}
export interface ToolUseFrame {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, unknown>;
  // 跟 thinking / text 一样的 (msg_uuid, index) — 用于 ChatStream 跨源去重
  // (历史 ∩ liveFrames 时按 msg_uuid 过滤). 历史接口 / 实时 SSE 都带; 旧
  // 流量缺字段时按 frame.id (tool_use_id) 兜底.
  msg_uuid?: string;
  index?: number;
}
export interface ToolResultFrame {
  type: "tool_result";
  tool_use_id: string;
  content: unknown; // 可以是 string 也可以是 [{type:"text", text}]
  is_error: boolean;
  msg_uuid?: string;
  index?: number;
}
export interface TaskStartedFrame {
  type: "task_started";
  task_id: string;
  subagent: string | null;
  description: string;
}
export interface TaskProgressFrame {
  type: "task_progress";
  task_id: string;
  description: string;
  last_tool: string | null;
}
export interface TaskDoneFrame {
  type: "task_done";
  task_id: string;
  status: string;
  summary: string | null;
}
export interface ResultFrame {
  type: "result";
  text: string | null;
  is_error: boolean;
  duration_ms: number | null;
  cost_usd: number | null;
  num_turns: number | null;
}
export interface ErrorFrame {
  type: "error";
  message: string;
}
export interface StreamEndFrame {
  type: "stream_end";
}

// 仅在 resume 流首帧出现: 后端把这轮 turn 的 user prompt 作为 snapshot 注入,
// 让刷新 / 切走再回 / 多 tab 能立刻看到"自己刚发的那条". 前端拿到后塞回
// localUserPrompt, 不进 liveFrames.
//
// attachment_count / inline_image_count 跟 text 配套: text 空 + 任一 > 0 时前端
// 渲染 "📎 N 个文件" / "🖼️ N 张图片" 占位 (各自独立 indicator, 可同时出现).
// 老 buffer / 老 SSE 流不带这俩字段, 默认按 0 处理.
export interface UserPromptFrame {
  type: "user_prompt";
  text: string;
  attachment_count?: number;
  inline_image_count?: number;
}

// backend make_can_use_tool 在 REGISTRY.register Future 之后推一帧, 告诉
// 前端"这个 tool_use 真的需要用户审批". 之前前端单看 tool_use 帧名字 ∈
// TOOLS_NEEDING_APPROVAL 静态名单, 跟 backend register 时机异步, auto 模式 LLM
// classifier 慢路径 (1-3s) 期间用户看到幽灵审批栏 → 点击 → 404. 现在前端只信
// 这帧才弹审批栏. ToolRow 仍用 isHitl 作视觉双保险.
export interface PermissionRequestFrame {
  type: "permission_request";
  tool_use_id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
}

// 后端在 POST /chat/permission 完成 resolve 后推一帧. 让审批栏立刻消失,
// ToolRow 回退到 in-progress (等真正的 tool_result 进来), 而不是傻等几分钟.
// 重连重放也带这一帧, 解决"刷新后看到已 resolve 的审批栏又被点一次→404"的 bug.
// decision="auto_session" 是 can_use_tool 命中本会话 allowlist 自动 allow 的快路径
// 推的, 不经过 HTTP route — 防止"点过 Allow session 后续同 verb 仍闪一下审批栏".
export interface PermissionResolvedFrame {
  type: "permission_resolved";
  tool_use_id: string;
  decision: "allow_once" | "allow_session" | "deny" | "auto_session";
}

// ──── workspace 授权工具特殊常量 ─────────────────────────────

export const WORKSPACE_PERMISSION_TOOL_NAME =
  "mcp__pentaloom__request_workspace_dir";

// 装 Python 包 (内联审批, 支持 allow_session — 同组合下次免审)
export const INSTALL_LIBS_TOOL_NAME =
  "mcp__pentaloom_env__install_python_libs";
// 跑 Python 脚本 (内联审批, 只有 allow_once / deny — 脚本内容每次都不同)
export const RUN_SCRIPT_TOOL_NAME =
  "mcp__pentaloom_env__run_python_script";
// 改 .pdf / .pptx 文件 (内联审批, 支持 allow_session — 同 path 本会话内只问一次).
// autofix=False 时后端 can_use_tool 直放, 不会走到这里.
export const FILE_VERIFY_TOOL_NAME =
  "mcp__pentaloom_files__file_verify";

// 装中文字体 Noto Sans SC 到系统 (内联审批, 一次性操作 — 只 allow_once / deny).
export const INSTALL_FONT_TOOL_NAME =
  "mcp__pentaloom__install_noto_sans_sc";

// 装 browser-use CLI / Chromium (内联审批, 支持 allow_session — 同 step 本会话内只问一次).
export const INSTALL_BROWSER_USE_TOOL_NAME =
  "mcp__pentaloom_browser__install_browser_use";
// 跑 browser-use CLI 子命令 (内联审批, 支持 allow_session — 同 action verb 本会话内只问一次).
export const BROWSER_USE_TOOL_NAME =
  "mcp__pentaloom_browser__browser_use";
// 通过 Chrome 扩展操控真实浏览器 (内联审批, 单 key — 首次任何 action 后整个会话所有 bridge 调用免审).
export const BROWSER_BRIDGE_TOOL_NAME =
  "mcp__pentaloom_browser_bridge__browser_bridge";
// macOS 桌面自动化 (内联审批, 单 key — 同 bridge 模式, 首次后整个会话免审).
export const COMPUTER_USE_TOOL_NAME =
  "mcp__pentaloom_computer__computer_use";
// 联网搜索 (内联审批, 单 key — 同 bridge 模式, 首次后整个会话免审).
export const WEB_SEARCH_TOOL_NAME =
  "mcp__pentaloom_search__web_search";

// weaver 元工具 — 沉淀 / 改 / 删 / 跑产物 (内联审批, **每次单审**, 不进 ALLOW_SESSION_TOOLS).
// 长期资产改动应该每次过目; list/inspect/tail_logs 是只读不审, 不在这里.
export const WEAVE_SKILL_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_skill";
export const WEAVE_APP_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_app";
// revise — draft / dirty 状态下覆盖 manifest / app.json / description, 单审一次.
export const WEAVE_APP_REVISE_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_app_revise";
// 递进式 weave 子工具 (auto-pass — app 主 weave HITL 通过后, 子操作免审).
export const WEAVE_APP_WRITE_FILE_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_app_write_file";
export const WEAVE_APP_EDIT_FILE_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_app_edit_file";
export const WEAVE_APP_FINALIZE_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_app_finalize";
export const EDIT_WEAVER_TOOL_NAME =
  "mcp__pentaloom_weaver__edit_weaver";
export const DELETE_WEAVER_TOOL_NAME =
  "mcp__pentaloom_weaver__delete_weaver";
export const RUN_WEAVER_TOOL_NAME =
  "mcp__pentaloom_weaver__run_weaver";
// invoke_app — 调 weaver app 的 invocation. 单 key "enabled" 模式 (同 web_search /
// browser_bridge / computer_use): 首次审完整会话所有 invoke_app 免审.
export const INVOKE_APP_TOOL_NAME =
  "mcp__pentaloom_weaver__invoke_app";
// dynamic workflow — 织流程 + 收口 + 调流程.
export const WEAVE_WORKFLOW_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_workflow";
export const WEAVE_WORKFLOW_FINALIZE_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_workflow_finalize";
// invoke_workflow 跟 invoke_app 同款 enabled-once 信任
export const INVOKE_WORKFLOW_TOOL_NAME =
  "mcp__pentaloom_weaver__invoke_workflow";
// invoke_workflow_dynamic — 动态版, 把 workflow 渲成 plan markdown 给主 agent 接管.
// 跟静态版分开常量, 但 enabled-once 行为一致.
export const INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME =
  "mcp__pentaloom_weaver__invoke_workflow_dynamic";
// ephemeral service 织造期工具 — 跟 invoke_app 同款 enabled-once 信任
export const WEAVE_SERVICE_START_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_service_start";
export const WEAVE_SERVICE_RESTART_TOOL_NAME =
  "mcp__pentaloom_weaver__weave_service_restart";
// window 开关 — agent 主动 open/close, 跟 invoke_app 同款 enabled-once
export const OPEN_APP_WINDOW_TOOL_NAME =
  "mcp__pentaloom_weaver__open_app_window";
export const CLOSE_APP_WINDOW_TOOL_NAME =
  "mcp__pentaloom_weaver__close_app_window";

export const BASH_TOOL_NAME = "Bash";
// SDK / CLI 内置 WebFetch — 拉单个 URL 抽信息. 单 key "enabled" 模式同 web_search.
export const WEB_FETCH_TOOL_NAME = "WebFetch";

// 需要 HITL 审批的工具名全集. 必须跟后端 pentaloom.tools.HITL_TOOL_NAMES 对齐.
// 全部走 FrameBlock 内联审批条 (卡片底部三按钮), 没有模态弹窗了.
export const TOOLS_NEEDING_APPROVAL: readonly string[] = [
  WORKSPACE_PERMISSION_TOOL_NAME,
  BASH_TOOL_NAME,
  WEB_FETCH_TOOL_NAME,
  INSTALL_LIBS_TOOL_NAME,
  RUN_SCRIPT_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
  INSTALL_FONT_TOOL_NAME,
  INSTALL_BROWSER_USE_TOOL_NAME,
  BROWSER_USE_TOOL_NAME,
  BROWSER_BRIDGE_TOOL_NAME,
  COMPUTER_USE_TOOL_NAME,
  WEB_SEARCH_TOOL_NAME,
  WEAVE_SKILL_TOOL_NAME,
  WEAVE_APP_TOOL_NAME,
  WEAVE_APP_REVISE_TOOL_NAME,
  WEAVE_WORKFLOW_TOOL_NAME,
  EDIT_WEAVER_TOOL_NAME,
  DELETE_WEAVER_TOOL_NAME,
  RUN_WEAVER_TOOL_NAME,
  INVOKE_APP_TOOL_NAME,
  INVOKE_WORKFLOW_TOOL_NAME,
  INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME,
  WEAVE_SERVICE_START_TOOL_NAME,
  WEAVE_SERVICE_RESTART_TOOL_NAME,
  OPEN_APP_WINDOW_TOOL_NAME,
  CLOSE_APP_WINDOW_TOOL_NAME,
];

// 支持 "allow_session" 决策的工具集合 — 跟后端 ALLOW_SESSION_TOOLS 对齐.
// UI 上对没在这里的工具隐藏 / 灰掉 "Allow session" 按钮.
export const ALLOW_SESSION_TOOLS: readonly string[] = [
  BASH_TOOL_NAME,
  WEB_FETCH_TOOL_NAME,
  INSTALL_LIBS_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
  INSTALL_BROWSER_USE_TOOL_NAME,
  BROWSER_USE_TOOL_NAME,
  BROWSER_BRIDGE_TOOL_NAME,
  COMPUTER_USE_TOOL_NAME,
  WEB_SEARCH_TOOL_NAME,
  INVOKE_APP_TOOL_NAME,
  INVOKE_WORKFLOW_TOOL_NAME,
  INVOKE_WORKFLOW_DYNAMIC_TOOL_NAME,
  WEAVE_SERVICE_START_TOOL_NAME,
  WEAVE_SERVICE_RESTART_TOOL_NAME,
  OPEN_APP_WINDOW_TOOL_NAME,
  CLOSE_APP_WINDOW_TOOL_NAME,
];

export interface WorkspacePermissionRequest {
  tool_use_id: string;
  path: string;
  reason: string;
}

export interface InstallLibsPermissionRequest {
  tool_use_id: string;
  libs: string[];
  reason: string;
}

export interface RunScriptPermissionRequest {
  tool_use_id: string;
  script_path: string;
  args: string[];
  description: string;
}

// ──── fs browse ─────────────────────────────────────────────

export interface FsEntry {
  name: string;
  path: string;
}

export interface BrowseResponse {
  path: string;          // 当前规范化后的绝对路径
  parent: string | null; // 上一级 (根目录时 null)
  home: string;          // $HOME, 给"回家"按钮
  entries: FsEntry[];
  truncated: boolean;
}

// POST /fs/open 的回执. 失败走 throw, 200 时只回实际打开的规范化路径.
export interface OpenFileResp {
  opened: string;
}

// weaver 4 个产物, 当前实装 Skill. 其他 3 类用空 list 占位.
// 工具调用走 SDK in-process MCP, 不走 REST; REST 只给 sidebar / 设置页面读 source.
export type WeaverSource = "builtin" | "agent_woven" | "user_imported" | "user_handwritten";

export interface SkillSummary {
  name: string;
  description: string;
  source: WeaverSource;
}

export type AppStatus = "draft" | "ready" | "dirty" | "failed";

export interface AppSummary {
  name: string;
  description: string;
  source: WeaverSource;
  status: AppStatus;  // 递进式 weave 状态机 (draft → ready → dirty / failed)
  invocation_count: number;
  has_app_definition: boolean;
  component_counts: Record<string, number>;  // {scripts: 2, windows: 1, ...}
}

// dynamic workflow — sidebar 用. 跟 AppSummary 平行的瘦摘要.
export interface WorkflowSummary {
  name: string;
  description: string;
  source: WeaverSource;
  status: AppStatus;  // 跟 app 共享状态机 (draft / ready / dirty / failed)
  step_count: number;
  use_count: number;
}

export interface WeaverProductsResponse {
  skills: SkillSummary[];
  subagents: unknown[];  // 占位
  workflows: WorkflowSummary[];
  apps: AppSummary[];
}

// /weaver/apps/{name}/detail 返回. AppDetailPanel 用. 跟后端 read_app_detail 对齐.
export interface AppInvocationSummary {
  id: string;
  description: string;
  target: { component: string; name: string; handler?: string | null } | null;
  input_keys: string[];
  output_keys: string[];
  timeout_ms: number;
  example_count: number;
}

export interface AppManifestSummary {
  name: string;
  type: string;
  version: string;
  invocations: AppInvocationSummary[];
  permissions: { network_hosts: string[]; file_paths: string[] };
  files: string[];  // relative paths (用 AppDetailResponse.files 拿 absolute)
  components?: Record<string, string[]>;  // {scripts: [...], windows: [...], ...}
}

export interface AppFileEntry {
  rel_path: string;
  absolute_path: string;
  ext: string;
  size: number;
}

export interface AppMeta {
  name: string;
  status: AppStatus;
  description: string;
  source: WeaverSource;
  created_at: string;
  updated_at: string;
  last_finalized_at: string | null;
  last_finalize_error: string | null;
  last_used_at: string | null;  // invoke_app 成功后递增 use_count 时一并写
  use_count: number;
  is_trusted: boolean;
}

export interface AppRunLog {
  run_id: string;
  invocation_id: string;
  status: string;       // success / failed / skipped (race / overlap)
  duration_ms: number;
  started_at: string;
  error?: string;
  trigger?: "user" | "schedule" | "watch" | "workflow";  // 旧 entry 缺字段默认 user
}

// launchd 接管后, service 状态由 read_app_detail.runtime row 拼出.
// 注意: launchd 不暴露 restart_count, 字段已废弃; started_at / uptime_seconds 由
// psutil.Process(pid).create_time() 算 — 进程不在 (status='dead') 时为 null.
export interface AppRunningService {
  name: string;
  status: "running" | "dead";
  port: number | null;            // 从 .runtime/<svc>.port 读, 没起过为 null
  pid: number | null;
  last_exit_status: number | null;
  started_at: number | null;      // unix ts; psutil 推断, 失败为 null
  uptime_seconds: number | null;  // now - started_at; 失败为 null
  log_path: string | null;        // launchd plist StandardOutPath; 没渲过 plist 为 null
}

// ephemeral service (agent 织造期 weave_service_start 起的 memory-only
// subprocess, 跟 declared launchd service 双轨). PentaLoom 重启全清.
// log_path 落盘在 sandbox/.ephemeral-logs/<svc>.log, 跨重启可看.
export interface AppEphemeralService {
  app_name: string;
  service_name: string;
  pid: number;
  port: number;
  started_at: number;
  uptime_s: number;
  log_path: string;
  alive: boolean;
  exit_code: number | null;
}

// E (watch): 单个 watch component 暴露的文件清单 — lazy fetch
export interface AppWatchEntry {
  rel_path: string;
  absolute_path: string;  // 给 openFile 用 (跟 AppFileEntry 同契约)
  size: number;
  mtime: number;  // unix ts
  is_dir: boolean;
}
export interface AppWatchFilesResponse {
  name: string;
  watch: string;
  path: string;
  entries: AppWatchEntry[];
  truncated: boolean;
  note?: string;
}

// schedule / watch trigger 运行态 — launchd plist 状态 + app.json join.
// 来自 read_app_detail; 后端 launchd plist 不直接暴露 events / debounce_ms (这些是
// 织造期声明, 非运行态), 真要看进 inspect_weaver. 这里只列 row 渲染必需字段.
export interface AppScheduleTrigger {
  name: string;
  schedule: string;              // cron 表达式 (从 app.json join)
  invocation_id: string;         // 从 app.json join
  loaded: boolean;               // launchctl 状态 — 是否在 launchd registry
  pid: number | null;            // launchd 起了 wrapper 才有 pid
  last_exit_status: number | null;
  next_fire_at: number | null;   // croniter 算; 表达式不合法时为 null
  last_fired_at: number | null;  // 倒读 runs.jsonl trigger='schedule' 拿
  in_flight: boolean;            // 推断: loaded + pid != null
  log_path: string | null;       // launchd plist StandardOutPath
}
export interface AppWatchTrigger {
  name: string;
  path: string;                  // 从 app.json join
  invocation_id: string;         // browse-only watch 后端不渲 plist 不会出现在这
  loaded: boolean;
  pid: number | null;
  last_exit_status: number | null;
  last_fired_at: number | null;  // 倒读 runs.jsonl trigger='watch'
  in_flight: boolean;
  log_path: string | null;
}
export interface AppTriggersState {
  schedules: AppScheduleTrigger[];
  watches: AppWatchTrigger[];
}

export interface AppDetailResponse {
  summary: AppManifestSummary;
  files: AppFileEntry[];
  meta: AppMeta | null;
  recent_runs: AppRunLog[];
  running_services?: AppRunningService[];  // D-4: 后端可能没返这字段, 前端默认空数组
  ephemeral_services?: AppEphemeralService[];  // 织造期 ephemeral, declared 之外的并列 section
  triggers?: AppTriggersState;             // schedules + watches 运行态
}

// ──── dynamic workflow ──────────────────────────────────

// 3 种 step (跟后端 models.py 的 InvokeAppStep / CallLlmStep / SetVarStep 平行).
// kind 是 discriminator, 其它字段按 kind 不同.
export type WorkflowStep =
  | { kind: "invoke_app"; id: string; app_name: string; invocation_id: string; args: Record<string, unknown> }
  | {
      kind: "call_llm";
      id: string;
      system: string;
      prompt: string;
      output_format: "text" | "json";
      model: string | null;
      max_tokens: number;
      timeout_s: number;
    }
  | { kind: "set_var"; id: string; value: Record<string, unknown> };

export interface WorkflowDefinition {
  name: string;
  description: string;
  version: string;
  inputs_schema: Record<string, unknown>;
  output_template: Record<string, unknown> | null;
  steps: WorkflowStep[];
}

export interface WorkflowMeta {
  name: string;
  description: string;
  source: WeaverSource;
  status: AppStatus;
  created_at: string;
  updated_at: string;
  last_finalized_at: string | null;
  last_finalize_error: string | null;
  last_used_at: string | null;
  use_count: number;
}

// 单步执行结果. status: success / failed (失败步还含 error). invoke_app 步还含
// app_name / invocation_id / app_run_id 给 modal 跳转 app modal 看那一次 app run.
// kind 多一个 "output_template": runtime 在 output_template 渲染失败时插一条 pseudo
// step 进 step_results, 让 modal 能看见错在哪 (而不是"step 全 success 但 run failed"幽灵).
export interface WorkflowStepLog {
  id: string;
  kind: "invoke_app" | "call_llm" | "set_var" | "output_template";
  status: "success" | "failed";
  duration_ms: number;
  output_summary?: unknown;
  error?: string;
  app_name?: string;
  invocation_id?: string;
  app_run_id?: string;
}

export interface WorkflowRunLog {
  run_id: string;
  status: string;       // success / failed
  duration_ms: number;
  started_at: string;
  trigger?: "user" | "schedule" | "watch" | "workflow";  // 当前 workflow 只支持 user 触发
  error?: string;
  steps: WorkflowStepLog[];
}

// /weaver/workflows/{name}/detail 返. summary 含 definition + meta + step 摘要.
export interface WorkflowDetailResponse {
  summary: {
    name: string;
    description: string;
    version: string;
    step_count: number;
    steps_summary: { id: string; kind: WorkflowStep["kind"] }[];
    definition: WorkflowDefinition;
    meta: WorkflowMeta | null;
  };
  recent_runs: WorkflowRunLog[];
}

// GET /fs/tree 返 — 右栏 WorkspaceTree 用. 嵌套树 (children 仅 directory 含).
// truncated=true 表示该目录触 max_depth, 还有未读子项 (UI 显 "..." 提示).
export interface FsTreeNode {
  name: string;
  path: string;
  is_directory: boolean;
  children?: FsTreeNode[] | null;
  truncated?: boolean;
}

// ──── 文件预览 ──────────────────────────────────────────

// GET /fs/preview/stat 返
export interface FilePreviewMeta {
  path: string;            // 规范化绝对路径
  name: string;
  size: number;
  ext: string;             // 不含点 . 的小写后缀; 无后缀 ""
  mtime: number;           // unix ts
  is_directory: boolean;
  is_binary_guess: boolean;
}

// GET /fs/preview/text 返
export interface TextPreviewResult {
  content: string;
  truncated: boolean;
  size: number;
}

// GET /fs/preview/office/xlsx 返 — openpyxl 后端结构化解析.
// 前端 HTML table 真渲 (sticky header / 字体颜色 / 背景色 / 合并单元格 / sheet tabs).
// docx / pptx 不走这, 它们 fetch ArrayBuffer 给客户端 docx-preview / pptx-renderer 库渲.
export interface XlsxCellStyle {
  bold?: boolean | null;
  italic?: boolean | null;
  font_size?: number | null;
  color?: string | null;       // CSS color, e.g. "#3d5a80"
  bg_color?: string | null;
  align?: "left" | "center" | "right" | null;
  valign?: "top" | "middle" | "bottom" | null;
}

export interface XlsxCell {
  text: string;
  style?: XlsxCellStyle | null;
}

export interface XlsxMerge {
  start_row: number;
  start_col: number;
  end_row: number;
  end_col: number;
}

export interface XlsxSheet {
  name: string;
  row_count: number;
  col_count: number;
  rows: XlsxCell[][];
  merges: XlsxMerge[];
  truncated: boolean;
}

export interface XlsxWorkbookPreview {
  sheets: XlsxSheet[];
  active_sheet_index: number;
  truncated: boolean;
  size: number;
}

// /fs/preview/archive/zip — 只列 metadata 不解压.
export interface ZipEntry {
  path: string;
  size: number;
  compressed_size: number;
  is_dir: boolean;
}

export interface ArchivePreview {
  entries: ZipEntry[];
  total_entries: number;
  truncated: boolean;
  size: number;
}

// /fs/preview/office/sqlite — 列表名 / 列名 / 前 200 行 / 行数.
export interface SqliteTable {
  name: string;
  columns: string[];
  rows: string[][];
  row_count: number;
  truncated: boolean;
}

export interface DatabasePreview {
  tables: SqliteTable[];
  total_tables: number;
  truncated: boolean;
  size: number;
}

// PATCH /sessions/{sid}/mounts: dirs / add / remove 三种用法之一即可.
export interface PatchMountsBody {
  dirs?: string[];
  add?: string[];
  remove?: string[];
}
