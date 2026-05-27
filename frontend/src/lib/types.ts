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
  uuid: string;
  frames: Frame[];
}

export interface PermissionDecisionBody {
  session_id: string;
  // allow_once: 仅这一次; allow_session: 加进本会话白名单 (仅 Bash 有效);
  // deny: 拒绝. workspace 工具上 allow_session 退化为 allow_once.
  decision: "allow_once" | "allow_session" | "deny";
}

export interface PermissionDecisionResp {
  session_id: string;
  tool_use_id: string;
  decision: "allow_once" | "allow_session" | "deny";
  added_path: string | null;
  added_bash_cmd: string | null;
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
  | StreamEndFrame;

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
}
export interface ToolResultFrame {
  type: "tool_result";
  tool_use_id: string;
  content: unknown; // 可以是 string 也可以是 [{type:"text", text}]
  is_error: boolean;
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

// ──── workspace 授权工具特殊常量 ─────────────────────────────

export const WORKSPACE_PERMISSION_TOOL_NAME =
  "mcp__pentaloom__request_workspace_dir";

// 需要 HITL 审批的工具名集合. 必须跟后端 pentaloom.tools.HITL_TOOL_NAMES 对齐.
// workspace 走模态弹窗 (WorkspacePermissionDialog), 其它 (如 Bash) 走内联 ToolUseBlock.
export const BASH_TOOL_NAME = "Bash";
export const TOOLS_NEEDING_APPROVAL: readonly string[] = [
  WORKSPACE_PERMISSION_TOOL_NAME,
  BASH_TOOL_NAME,
];

export interface WorkspacePermissionRequest {
  tool_use_id: string;
  path: string;
  reason: string;
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
