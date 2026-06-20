// Package control — loomer 进程内的本地 HTTP control server, 给 agent 反向拉
// per-window 状态用. listen 在 127.0.0.1:<random_port>, 只走 loopback 不暴露
// 网络. main.go 起 server 后把 port 写进 ready NDJSON, loom daemon 记录到
// window registry, agent tool 调 socket 拿 port 后 httpx GET 走过来.
//
// 当前 endpoint:
//   GET /logs?lines=<n>  → JSON: {"entries": [{time, level, args}, ...]}
//
// 后续会加 /screenshot 返 PNG bytes.
package control

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/guyi-a/PentaLoom/loomer/internal/logbuf"
)

// ScreenshotFunc: 调用方注入的截图实现 — 返 PNG bytes 或错. 因为 ui 包知道
// NSWindow 句柄但 control 包不知道, 把"截图实现"通过函数参数注入进来.
// 实测函数会 dispatch 到主线程同步执行, control HTTP handler goroutine 阻塞
// 到 PNG 出来 (~100ms 量级).
type ScreenshotFunc func() ([]byte, error)

// Server 包 net/http server + 共享 state.
type Server struct {
	logRing    *logbuf.Ring
	screenshot ScreenshotFunc
	listener   net.Listener
	httpSrv    *http.Server
}

// New 创建 server, listen 在 127.0.0.1 随机端口. 调用方拿 .Port() 后传给
// loom daemon 注册. 调 .Serve() 阻塞运行 (一般跑 goroutine), .Close() 优雅停.
// screenshotFn 为 nil 时 /screenshot endpoint 返 501 not-implemented (跨平台/
// 测试场景方便, 不强制提供).
func New(logRing *logbuf.Ring, screenshotFn ScreenshotFunc) (*Server, error) {
	if logRing == nil {
		return nil, fmt.Errorf("control.New: logRing required")
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("control.New: listen 127.0.0.1:0 failed: %w", err)
	}
	mux := http.NewServeMux()
	srv := &Server{
		logRing:    logRing,
		screenshot: screenshotFn,
		listener:   ln,
		httpSrv: &http.Server{
			Handler:           mux,
			ReadHeaderTimeout: 5 * time.Second,
		},
	}
	mux.HandleFunc("/logs", srv.handleLogs)
	mux.HandleFunc("/screenshot", srv.handleScreenshot)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	return srv, nil
}

// Port 返 listener 实际绑定的端口.
func (s *Server) Port() int {
	return s.listener.Addr().(*net.TCPAddr).Port
}

// Serve 阻塞运行 HTTP server. 通常 main 起 goroutine 跑.
func (s *Server) Serve() error {
	err := s.httpSrv.Serve(s.listener)
	if err == http.ErrServerClosed {
		return nil
	}
	return err
}

// Close 优雅关闭, 5s 超时.
func (s *Server) Close() error {
	return s.httpSrv.Close()
}

// handleLogs: GET /logs?lines=<n> → 返 ring 末 N 条. lines 缺省 50.
func (s *Server) handleLogs(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	n := 50
	if v := r.URL.Query().Get("lines"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			n = parsed
		}
	}
	entries := s.logRing.Tail(n)
	w.Header().Set("Content-Type", "application/json")
	resp := map[string]any{"entries": entries}
	if err := json.NewEncoder(w).Encode(resp); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
}

// handleScreenshot: GET /screenshot → 返 image/png raw bytes. screenshot fn 没注入
// 返 501 (跨平台 / 测试场景). agent 端拿 PNG bytes 后 base64 编进 MCP image content
// + 写文件双写.
func (s *Server) handleScreenshot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if s.screenshot == nil {
		http.Error(w, "screenshot not supported on this platform", http.StatusNotImplemented)
		return
	}
	png, err := s.screenshot()
	if err != nil {
		http.Error(w, "screenshot failed: "+err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Content-Length", strconv.Itoa(len(png)))
	if _, err := w.Write(png); err != nil {
		// header 已写不能再 Error(); log 一下让 HTTP server 自然结束 conn.
		// (loomer 没框架 logger, stderr 留底.)
		fmt.Fprintf(os.Stderr, "control: write screenshot body: %v\n", err)
	}
}
