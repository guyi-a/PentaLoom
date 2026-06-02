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

export interface HistoryMessage {
  role: "user" | "assistant";
  uuid: string;                       // SDK envelope uuid - 用作 React key
  message_id: string | null;          // anthropic message.id - 跨源去重 key (user 历史可能为 null)
  frames: Frame[];
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
export interface UserPromptFrame {
  type: "user_prompt";
  text: string;
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
// 递进式 weave 子工具 (Phase B.5, auto-pass — app 主 weave HITL 通过后, 子操作免审).
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
  EDIT_WEAVER_TOOL_NAME,
  DELETE_WEAVER_TOOL_NAME,
  RUN_WEAVER_TOOL_NAME,
  INVOKE_APP_TOOL_NAME,
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

// weaver 4 个产物里 M14 唯一实装的是 Skill. 其他 3 类用空 list 占位, M16/M17/M18 上线.
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

export interface WeaverProductsResponse {
  skills: SkillSummary[];
  subagents: unknown[];  // M17
  workflows: unknown[];  // workflow milestone
  apps: AppSummary[];
}

// PATCH /sessions/{sid}/mounts: dirs / add / remove 三种用法之一即可.
export interface PatchMountsBody {
  dirs?: string[];
  add?: string[];
  remove?: string[];
}
