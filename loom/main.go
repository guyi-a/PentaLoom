// loom — daemon for PentaLoom invocable apps.
//
// 6 个子命令:
//   loom start        — daemon 模式, listen ~/.pentaloom/loom.sock, 由 launchd 起.
//   loom install      — 写 ~/Library/LaunchAgents/com.pentaloom.loom.plist + launchctl load.
//   loom uninstall    — launchctl unload + 删 plist.
//   loom open ...     — IPC client, 给跑着的 daemon 发 window.open.
//   loom close <id>   — IPC client, 发 window.close.
//   loom status       — IPC client, 列已开 windows.
//
// 用 stdlib flag, 子命令在 main 里 switch, 不引 cobra.
package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/guyi-a/PentaLoom/loom/internal/protocol"
	"github.com/guyi-a/PentaLoom/loom/internal/registry"
	"github.com/guyi-a/PentaLoom/loom/internal/socket"
)

//go:embed plist/loom.plist.tmpl
var plistTmpl string

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	cmd := os.Args[1]
	rest := os.Args[2:]

	switch cmd {
	case "start":
		mustOK(cmdStart(rest))
	case "install":
		mustOK(cmdInstall())
	case "uninstall":
		mustOK(cmdUninstall())
	case "open":
		mustOK(cmdOpen(rest))
	case "close":
		mustOK(cmdClose(rest))
	case "status":
		mustOK(cmdStatus())
	case "help", "-h", "--help":
		usage()
	default:
		fmt.Fprintf(os.Stderr, "loom: unknown command %q\n\n", cmd)
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `loom — PentaLoom invocable app daemon.

Usage:
  loom start                                start daemon (Unix socket loop, called by launchd)
  loom install                              install + load launchd UserAgent (~/Library/LaunchAgents)
  loom uninstall                            unload + remove launchd plist
  loom open --entry <path> [flags]          open a window via running daemon
  loom close <window-id>                    close a window
  loom status                               list active windows

Open flags:
  --entry <path>      .tsx entry file (required, must be absolute or relative to cwd)
  --title <text>      window title (default: entry filename)
  --width <n>         window width  (default: 800)
  --height <n>        window height (default: 600)

Paths:
  socket  ~/.pentaloom/loom.sock
  bin     ~/.pentaloom/bin/loom
  logs    ~/.pentaloom/logs/loom.{stdout,stderr}.log
  plist   ~/Library/LaunchAgents/com.pentaloom.loom.plist
`)
}

func mustOK(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "loom:", err)
		os.Exit(1)
	}
}

// ─── start: daemon 模式 ─────────────────────────────────────────────

func cmdStart(_ []string) error {
	socketPath := defaultSocketPath()
	loomerBin := defaultLoomerPath()

	// 让 daemon 自己 lazy 创建 logs / sock 父目录.
	reg := registry.New(loomerBin)

	handler := func(ctx context.Context, req *protocol.Request) *protocol.Response {
		switch req.Cmd {
		case protocol.CmdWindowOpen:
			return handleOpen(reg, req)
		case protocol.CmdWindowClose:
			return handleClose(reg, req)
		case protocol.CmdWindowList:
			return handleList(reg, req)
		case protocol.CmdWindowInvoke:
			return handleInvoke(reg, req)
		default:
			return &protocol.Response{Ok: false, Error: "unknown cmd: " + req.Cmd}
		}
	}

	srv := socket.NewServer(socketPath, handler)
	srv.OnReady = func() {
		// socket 真可 dial 了再 print, 否则 caller (Makefile / start-dev.sh) race
		// 看到 "started" 就 dial 会撞 ENOENT.
		fmt.Printf("loom: started, listening on %s (loomer=%s)\n", socketPath, loomerBin)
	}

	// 信号处理: SIGINT/SIGTERM → 取消 ctx, listener close, KillAll loomer 子进程.
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	defer func() {
		reg.KillAll()
		fmt.Println("loom: stopped")
	}()
	return srv.Listen(ctx)
}

func handleOpen(reg *registry.Registry, req *protocol.Request) *protocol.Response {
	var p protocol.WindowOpenReq
	if err := json.Unmarshal(req.Data, &p); err != nil {
		return &protocol.Response{Ok: false, Error: "bad open payload: " + err.Error()}
	}
	if p.EntryPath == "" {
		return &protocol.Response{Ok: false, Error: "entry_path required"}
	}
	wp, err := reg.Open(&p)
	if err != nil {
		return &protocol.Response{Ok: false, Error: err.Error()}
	}
	data, _ := json.Marshal(protocol.WindowOpenResp{WindowID: wp.ID, PID: wp.PID})
	return &protocol.Response{Ok: true, Data: data}
}

func handleClose(reg *registry.Registry, req *protocol.Request) *protocol.Response {
	var p protocol.WindowCloseReq
	if err := json.Unmarshal(req.Data, &p); err != nil {
		return &protocol.Response{Ok: false, Error: "bad close payload: " + err.Error()}
	}
	// 二选一: 优先 WindowID, 否则按 (App, WindowName) 二级索引.
	if p.WindowID != "" {
		if err := reg.Close(p.WindowID); err != nil {
			return &protocol.Response{Ok: false, Error: err.Error()}
		}
		return &protocol.Response{Ok: true}
	}
	if p.App != "" && p.WindowName != "" {
		n := reg.CloseByName(p.App, p.WindowName)
		if n == 0 {
			return &protocol.Response{Ok: false, Error: fmt.Sprintf("no window with app=%s window_name=%s", p.App, p.WindowName)}
		}
		return &protocol.Response{Ok: true}
	}
	return &protocol.Response{Ok: false, Error: "close requires window_id OR (app + window_name)"}
}

func handleList(reg *registry.Registry, req *protocol.Request) *protocol.Response {
	var p protocol.WindowListReq
	if len(req.Data) > 0 {
		_ = json.Unmarshal(req.Data, &p)
	}
	list := reg.List(p.App)
	data, _ := json.Marshal(protocol.WindowListResp{Windows: list})
	return &protocol.Response{Ok: true, Data: data}
}

func handleInvoke(reg *registry.Registry, req *protocol.Request) *protocol.Response {
	var p protocol.WindowInvokeReq
	if err := json.Unmarshal(req.Data, &p); err != nil {
		return &protocol.Response{Ok: false, Error: "bad invoke payload: " + err.Error()}
	}
	if p.App == "" || p.WindowName == "" || p.InvocationID == "" {
		return &protocol.Response{Ok: false, Error: "invoke requires app + window_name + invocation_id"}
	}
	output, err := reg.Invoke(p.App, p.WindowName, p.InvocationID, p.Args, p.TimeoutMs)
	if err != nil {
		return &protocol.Response{Ok: false, Error: err.Error()}
	}
	data, _ := json.Marshal(protocol.WindowInvokeResp{Output: output})
	return &protocol.Response{Ok: true, Data: data}
}
