// PentaLoom 前后端数据契约.
// 跟 agent/pentaloom/routers/{chat,sessions}.py 的 pydantic / SSE frame 一一对应.
// 改一边记得改另一边.

// ──── REST ─────────────────────────────────────────────────────

export interface SessionMeta {
  session_id: string;
  title: string | null;
  mounted_dirs: string[];
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
  | UserPromptFrame;

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

export const BASH_TOOL_NAME = "Bash";

// 需要 HITL 审批的工具名全集. 必须跟后端 pentaloom.tools.HITL_TOOL_NAMES 对齐.
// 全部走 FrameBlock 内联审批条 (卡片底部三按钮), 没有模态弹窗了.
export const TOOLS_NEEDING_APPROVAL: readonly string[] = [
  WORKSPACE_PERMISSION_TOOL_NAME,
  BASH_TOOL_NAME,
  INSTALL_LIBS_TOOL_NAME,
  RUN_SCRIPT_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
  INSTALL_FONT_TOOL_NAME,
];

// 支持 "allow_session" 决策的工具集合 — 跟后端 ALLOW_SESSION_TOOLS 对齐.
// UI 上对没在这里的工具隐藏 / 灰掉 "Allow session" 按钮.
export const ALLOW_SESSION_TOOLS: readonly string[] = [
  BASH_TOOL_NAME,
  INSTALL_LIBS_TOOL_NAME,
  FILE_VERIFY_TOOL_NAME,
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
