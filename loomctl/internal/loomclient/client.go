// Package loomclient 是 loomctl → loom Unix socket 的最小 client.
//
// 故意不依赖 loom module — loomctl 是给 weaver app 脚本用的反向调用 CLI, 装到
// ~/.pentaloom/bin/, 跟 loom 同级独立部署. 如果引用 loom 内部 package, 一来 Go
// module path 拗口, 二来 loom protocol 字段微调时 loomctl 也得跟着 rebuild —
// 现在 protocol 字段全 JSON 兼容, 复制一份字面定义反而稳.
package loomclient

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
)

// ─── 协议字段 (跟 loom/internal/protocol 镜像) ──────────────────────────

const (
	CmdWindowOpen  = "window.open"
	CmdWindowClose = "window.close"
	CmdWindowList  = "window.list"
)

type Request struct {
	ID   string          `json:"id"`
	Cmd  string          `json:"cmd"`
	Data json.RawMessage `json:"data,omitempty"`
}

type Response struct {
	ID    string          `json:"id"`
	Ok    bool            `json:"ok"`
	Error string          `json:"error,omitempty"`
	Data  json.RawMessage `json:"data,omitempty"`
}

type WindowOpenReq struct {
	EntryPath  string `json:"entry_path"`
	Title      string `json:"title,omitempty"`
	Width      int    `json:"width,omitempty"`
	Height     int    `json:"height,omitempty"`
	App        string `json:"app,omitempty"`
	WindowName string `json:"window_name,omitempty"`
}

type WindowOpenResp struct {
	WindowID string `json:"window_id"`
	PID      int    `json:"pid"`
}

type WindowCloseReq struct {
	WindowID   string `json:"window_id,omitempty"`
	App        string `json:"app,omitempty"`
	WindowName string `json:"window_name,omitempty"`
}

type WindowInfo struct {
	WindowID   string `json:"window_id"`
	PID        int    `json:"pid"`
	EntryPath  string `json:"entry_path"`
	Title      string `json:"title"`
	App        string `json:"app,omitempty"`
	WindowName string `json:"window_name,omitempty"`
	StartedAt  int64  `json:"started_at"`
}

type WindowListReq struct {
	App string `json:"app,omitempty"`
}

type WindowListResp struct {
	Windows []WindowInfo `json:"windows"`
}

// ─── socket client ──────────────────────────────────────────────────

// DefaultSocketPath: ~/.pentaloom/loom.sock — 跟 loom server 默认对齐.
func DefaultSocketPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".pentaloom", "loom.sock")
}

// Send: 单 request → 单 response. 失败 (daemon 没起 / socket 文件不在 / 网络错)
// 直接返 err, 业务错 (resp.Ok=false) 当成功 RPC 返回, 调用方按 resp.Ok 分支.
func Send(socketPath string, req *Request) (*Response, error) {
	conn, err := net.Dial("unix", socketPath)
	if err != nil {
		return nil, fmt.Errorf("dial %s: %w (loom daemon running? try `loom install` or `make loom-install`)", socketPath, err)
	}
	defer conn.Close()

	b, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}
	if _, err := conn.Write(append(b, '\n')); err != nil {
		return nil, fmt.Errorf("write: %w", err)
	}

	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	if !scanner.Scan() {
		return nil, fmt.Errorf("read response: %w", scanner.Err())
	}

	var resp Response
	if err := json.Unmarshal(scanner.Bytes(), &resp); err != nil {
		return nil, fmt.Errorf("parse response: %w (raw: %q)", err, scanner.Text())
	}
	return &resp, nil
}
