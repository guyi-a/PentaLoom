// Package socket 包 Unix socket server + client. JSON-line 协议, 一行 = 一个 request 或
// response. 长连接也支持 (一个 conn 上多个 request), 但 PR 1 每个 client 命令打开新 conn
// 发一条收一条关 — 简单稳, 不必持久连接.
package socket

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"sync"

	"github.com/guyi-a/PentaLoom/loom/internal/protocol"
)

// Handler 处理一个 request, 返 Response.
//   - 业务错误: Resp.Ok=false + Error 字符串
//   - server 框架错误 (e.g. 序列化失败) 由 socket 层兜底, 不进 Handler
type Handler func(ctx context.Context, req *protocol.Request) *protocol.Response

// Server: Unix socket listener + accept loop.
type Server struct {
	socketPath string
	handler    Handler
	listener   net.Listener
	wg         sync.WaitGroup
	// OnReady: socket 文件创建 + chmod 完成后调一次, 然后 server 进 accept loop.
	// daemon main 用它打印 "started" 行, 保证 print 时 socket 真已可连 — 否则
	// caller race 把 dial 抢在 listen 完成之前.
	OnReady func()
}

// NewServer: socketPath 通常 ~/.pentaloom/loom.sock.
func NewServer(socketPath string, h Handler) *Server {
	return &Server{socketPath: socketPath, handler: h}
}

// Listen: 创建 socket 文件 (清理旧的) + 进 accept loop. 阻塞调用, ctx 取消时优雅退出.
func (s *Server) Listen(ctx context.Context) error {
	// 旧 socket 文件清理 — daemon 上次 crash 没 cleanup 时, 不删的话 net.Listen 会
	// "address already in use". 删之前确认目标确实是 socket (防误删别的文件).
	if fi, err := os.Stat(s.socketPath); err == nil && fi.Mode()&os.ModeSocket != 0 {
		_ = os.Remove(s.socketPath)
	}

	if err := os.MkdirAll(filepath.Dir(s.socketPath), 0o755); err != nil {
		return fmt.Errorf("mkdir socket parent: %w", err)
	}
	l, err := net.Listen("unix", s.socketPath)
	if err != nil {
		return fmt.Errorf("listen %s: %w", s.socketPath, err)
	}
	s.listener = l
	// 0700 — 只允许当前 user 读写. 防其他用户连这个 daemon 触发 IPC.
	_ = os.Chmod(s.socketPath, 0o600)

	// 此时 socket 真可连 — 通知 caller 可以打印 "started" 之类.
	if s.OnReady != nil {
		s.OnReady()
	}

	// ctx 取消 → 关 listener 让 Accept 返 err, 退出循环.
	go func() {
		<-ctx.Done()
		_ = l.Close()
	}()

	for {
		conn, err := l.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				break
			}
			// 非致命错 (e.g. 单 conn ECONNRESET) 继续 accept, 否则退.
			return fmt.Errorf("accept: %w", err)
		}
		s.wg.Add(1)
		go s.handleConn(ctx, conn)
	}
	s.wg.Wait()
	return nil
}

func (s *Server) handleConn(ctx context.Context, conn net.Conn) {
	defer s.wg.Done()
	defer conn.Close()

	scanner := bufio.NewScanner(conn)
	// 默认 64KB 一行够; 防大 payload 截断, 给 1MB.
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)

	for scanner.Scan() {
		line := scanner.Bytes()
		var req protocol.Request
		if err := json.Unmarshal(line, &req); err != nil {
			writeResp(conn, &protocol.Response{
				Ok:    false,
				Error: fmt.Sprintf("malformed request: %v", err),
			})
			continue
		}
		resp := s.handler(ctx, &req)
		if resp == nil {
			resp = &protocol.Response{ID: req.ID, Ok: false, Error: "handler returned nil"}
		}
		resp.ID = req.ID // 兜底: handler 忘记带 ID 时补.
		writeResp(conn, resp)
	}
}

func writeResp(conn net.Conn, resp *protocol.Response) {
	b, err := json.Marshal(resp)
	if err != nil {
		// 兜底: marshal 失败也得给 client 一个 raw response 防它卡 read.
		fallback := fmt.Sprintf(`{"id":%q,"ok":false,"error":"marshal failed: %s"}`, resp.ID, err)
		_, _ = conn.Write([]byte(fallback + "\n"))
		return
	}
	_, _ = conn.Write(append(b, '\n'))
}

// Send: client 端 — 单次 connect + 发 request + 读一行 response + 关 conn.
// 简单同步调用. PR 1 client 没并发需求.
func Send(socketPath string, req *protocol.Request) (*protocol.Response, error) {
	conn, err := net.Dial("unix", socketPath)
	if err != nil {
		return nil, fmt.Errorf("dial %s: %w (daemon running?)", socketPath, err)
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

	var resp protocol.Response
	if err := json.Unmarshal(scanner.Bytes(), &resp); err != nil {
		return nil, fmt.Errorf("parse response: %w (raw: %q)", err, scanner.Text())
	}
	return &resp, nil
}
