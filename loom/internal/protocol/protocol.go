// Package protocol 定义 loom daemon 跟 client (loom CLI 子命令) 之间的 JSON-line
// 消息格式. 协议设计为单 request → 单 response, 简单 NDJSON 无状态.
//
// PR 1 三个命令: window.open / window.close / window.list. 后续 PR 加 service.*.
package protocol

import "encoding/json"

// 命令名 — 用 namespace 风格 (window.open) 防止扩展时重名.
const (
	CmdWindowOpen   = "window.open"
	CmdWindowClose  = "window.close"
	CmdWindowList   = "window.list"
	CmdWindowInvoke = "window.invoke"
)

// Request: client → daemon. ID 由 client 生成, daemon 原样回 (用于关联多 in-flight).
type Request struct {
	ID   string          `json:"id"`
	Cmd  string          `json:"cmd"`
	Data json.RawMessage `json:"data,omitempty"`
}

// Response: daemon → client. Ok=false 时 Error 非空, Data 通常是 null.
type Response struct {
	ID    string          `json:"id"`
	Ok    bool            `json:"ok"`
	Error string          `json:"error,omitempty"`
	Data  json.RawMessage `json:"data,omitempty"`
}

// ── window.open payload ─────────────────────────────────────────────

// WindowOpenReq: client 发给 daemon. EntryPath 必须绝对路径
// (CLI 在发送前 filepath.Abs, daemon 端不解析相对路径,
// 防止 client / daemon cwd 不一致引起的歧义).
//
// App / WindowName 是 M22 P1 加的逻辑标识 — schedule script 想 close 兄弟 window
// 时只知道 (app, window_name), 不留 daemon 给的 window_id. registry 用这俩做二级
// 索引, close-by-name 路径走它.
type WindowOpenReq struct {
	EntryPath  string `json:"entry_path"`
	Title      string `json:"title,omitempty"`
	Width      int    `json:"width,omitempty"`
	Height     int    `json:"height,omitempty"`
	App        string `json:"app,omitempty"`         // weaver app name; loomctl 必填
	WindowName string `json:"window_name,omitempty"` // app.json components.windows[].name

	// floating widget 4 件套. registry.Open 把这些翻成 loomer CLI flag.
	// Titlebar="" / "normal" 普通窗 (默认); "hidden" 整个 titlebar 没了.
	// Movable nil 时跟 titlebar 联动: hidden→true, normal→false.
	Titlebar    string `json:"titlebar,omitempty"`
	Transparent bool   `json:"transparent,omitempty"`
	AlwaysOnTop bool   `json:"always_on_top,omitempty"`
	Movable     *bool  `json:"movable,omitempty"` // pointer 让 nil/false 区分: nil = 跟 titlebar 联动
}

// WindowOpenResp: daemon 返给 client.
type WindowOpenResp struct {
	WindowID string `json:"window_id"` // daemon 内部 id, 用作后续 close 的 handle
	PID      int    `json:"pid"`       // loomer 子进程 PID
}

// ── window.close payload ────────────────────────────────────────────

// WindowCloseReq: 二选一. WindowID 直接关; (App, WindowName) 走 registry 二级索引.
type WindowCloseReq struct {
	WindowID   string `json:"window_id,omitempty"`
	App        string `json:"app,omitempty"`
	WindowName string `json:"window_name,omitempty"`
}

// WindowCloseResp: 空 OK 即可, daemon 直接返 Ok=true.

// ── window.list payload ─────────────────────────────────────────────

type WindowInfo struct {
	WindowID   string `json:"window_id"`
	PID        int    `json:"pid"`
	EntryPath  string `json:"entry_path"`
	Title      string `json:"title"`
	App        string `json:"app,omitempty"`
	WindowName string `json:"window_name,omitempty"`
	StartedAt  int64  `json:"started_at"` // unix seconds
	// ControlPort: loomer 进程内 control HTTP server 的 listen 端口 (loopback).
	// agent 拿到后 httpx GET 127.0.0.1:port/{logs,screenshot} 反向拉日志/截图.
	// 0 表示 loomer 还没发 ready msg (启动早期), agent 应该重试.
	ControlPort int `json:"control_port,omitempty"`
}

// WindowListReq: 可选过滤. App="" 时返全部.
type WindowListReq struct {
	App string `json:"app,omitempty"`
}

type WindowListResp struct {
	Windows []WindowInfo `json:"windows"`
}

// ── window.invoke payload ───────────────────────────────────────────
//
// agent (Python) → loom socket → loomer stdin → JS registered handler
// → loomer stdout → loom 异步 channel → socket response → agent.
//
// (App, WindowName) 二级索引找已开的 loomer 子进程, 通过它的 stdin pipe
// 推 NDJSON invoke msg. 等 loomer stdout 上 result NDJSON (按 RequestID
// 关联). TimeoutMs 满则返 timeout 错, **不 kill loomer** — handler 慢
// 不该让用户窗死.

type WindowInvokeReq struct {
	App          string          `json:"app"`
	WindowName   string          `json:"window_name"`
	InvocationID string          `json:"invocation_id"`
	Args         json.RawMessage `json:"args,omitempty"`
	TimeoutMs    int             `json:"timeout_ms,omitempty"` // 0 → daemon 默认 30s
}

type WindowInvokeResp struct {
	Output json.RawMessage `json:"output,omitempty"`
}

// ── loomer ↔ loom stdio NDJSON (window.invoke 子协议) ──────────────
//
// loom → loomer (stdin): {"type":"invoke", "request_id":"...", "invocation_id":"...", "args":{...}}
// loomer → loom (stdout): {"type":"result", "request_id":"...", "output":{...}}
//                  或   : {"type":"result", "request_id":"...", "error":"..."}
//
// 第一行 "ready" 是 loomer 启动握手 (告诉 loom: webview 准备好接 invoke 了);
// 现阶段 loom 不强制等 ready, 收到 invoke 才有意义. 留作未来 health probe.

type LoomerMsg struct {
	Type         string          `json:"type"`
	RequestID    string          `json:"request_id,omitempty"`
	InvocationID string          `json:"invocation_id,omitempty"`
	Args         json.RawMessage `json:"args,omitempty"`
	Output       json.RawMessage `json:"output,omitempty"`
	Error        string          `json:"error,omitempty"`
	// ControlPort: 仅 type=ready 时填. loomer 内 control HTTP server 的 port,
	// daemon 存进 WindowProc 让 window.list 能透传给 agent.
	ControlPort int `json:"control_port,omitempty"`
}
