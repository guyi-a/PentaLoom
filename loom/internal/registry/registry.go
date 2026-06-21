// Package registry 管 loomer 子进程: spawn / 监管 / list / kill / invoke.
//
// in-memory map[id]*WindowProc, daemon 重启全丢.
// daemon 退出时所有 loomer 子进程也跟着死 — go runtime 自动收回, macOS
// 不需要手动 setpgid (Unix socket cleanup 已经够).
//
// invoke 协议: 给已开窗 spawn 时建好 stdin / stdout pipe + reader goroutine,
// 写一行 NDJSON 给 loomer stdin, 等 loomer stdout 上同 RequestID 的 result
// 行回来. pendingInvocations map[reqID]chan 跨 goroutine 异步等回.
package registry

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
	"sync"
	"sync/atomic"
	"time"

	"github.com/guyi-a/PentaLoom/loom/internal/protocol"
)

// 默认 invoke 超时 — caller 没给 TimeoutMs 时用. 跟 manifest invocation.timeout_ms
// 默认对齐 (30s, 长 invocation 也够; handler 真要长时间用 args 自己 chunk).
const defaultInvokeTimeout = 30 * time.Second

// pendingInvocation: 一次 in-flight invoke 等待结果的 channel.
type pendingInvocation struct {
	resultCh chan invokeOutcome
}

type invokeOutcome struct {
	output json.RawMessage
	err    error // 非 nil 时 output 必空
}

// WindowProc: 一个 loomer 子进程的 in-memory 记录.
type WindowProc struct {
	ID         string
	PID        int
	EntryPath  string
	Title      string
	App        string
	WindowName string
	StartedAt  time.Time
	cmd        *exec.Cmd     // 内部句柄, 给 Kill / Wait 用; 不暴露给外部
	stdin      io.WriteCloser // 写 NDJSON 给 loomer (invoke msg)
	stdinMu    sync.Mutex     // 串行化 stdin 写 — 多 goroutine 并发 invoke 同窗时

	// ControlPort: loomer ready msg 上来后填; 写一次 (readLoop) 读多次 (ToInfo).
	// 用 atomic 避锁, 0 表示 loomer 还没发 ready (启动早期).
	controlPort atomic.Int32
}

// Registry: 进程内 thread-safe 注册表.
type Registry struct {
	mu        sync.Mutex
	loomerBin string
	windows   map[string]*WindowProc

	pendMu     sync.Mutex
	pending    map[string]*pendingInvocation // RequestID → ch
}

func New(loomerBin string) *Registry {
	return &Registry{
		loomerBin: loomerBin,
		windows:   make(map[string]*WindowProc),
		pending:   make(map[string]*pendingInvocation),
	}
}

// Open: spawn 一个 loomer 子进程渲指定 entry, 注册返新 window id + PID.
// title/width/height 任一为零值时不传 flag, 让 loomer 用默认.
//
// stdin / stdout 走 pipe (不再 nil) — invoke 协议需要. stderr 仍不接,
// 让 loomer 自己 print 到自己进程的 stderr, launchd / 用户终端能直接看.
func (r *Registry) Open(req *protocol.WindowOpenReq) (*WindowProc, error) {
	args := []string{"--entry", req.EntryPath}
	if req.Title != "" {
		args = append(args, "--title", req.Title)
	}
	if req.Width > 0 {
		args = append(args, "--width", fmt.Sprintf("%d", req.Width))
	}
	if req.Height > 0 {
		args = append(args, "--height", fmt.Sprintf("%d", req.Height))
	}

	// floating widget 4 件套 — 透给 loomer flag. Movable nil 时跟 titlebar 联动:
	// hidden 默认 movable=true (borderless 必备), normal 默认 false (titlebar
	// 区域已经能拖, 全屏可拖反而干扰用户选文本).
	if req.Titlebar == "hidden" {
		args = append(args, "--titlebar=hidden")
	}
	if req.Transparent {
		args = append(args, "--transparent")
	}
	if req.AlwaysOnTop {
		args = append(args, "--always-on-top")
	}
	movable := req.Titlebar == "hidden" // 默认联动
	if req.Movable != nil {
		movable = *req.Movable
	}
	if movable {
		args = append(args, "--movable")
	}

	cmd := exec.Command(r.loomerBin, args...)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("loomer stdin pipe: %w", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		_ = stdin.Close()
		return nil, fmt.Errorf("loomer stdout pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		_ = stdin.Close()
		_ = stdout.Close()
		return nil, fmt.Errorf("spawn loomer: %w", err)
	}

	id := newWindowID()
	wp := &WindowProc{
		ID:         id,
		PID:        cmd.Process.Pid,
		EntryPath:  req.EntryPath,
		Title:      req.Title,
		App:        req.App,
		WindowName: req.WindowName,
		StartedAt:  time.Now(),
		cmd:        cmd,
		stdin:      stdin,
	}

	r.mu.Lock()
	r.windows[id] = wp
	r.mu.Unlock()

	// reader goroutine: 解析 loomer stdout NDJSON, 把 result 行路由回对应
	// pending channel. loomer 退出 / stdin 关 → readLoop EOF 退, reaper 收尸.
	go r.readLoop(wp, stdout)

	// reaper: 子进程 exit 时清表 + 取消该 window 上所有 pending invocation.
	go func() {
		_ = cmd.Wait() // 阻塞到 exit
		r.mu.Lock()
		if cur, ok := r.windows[id]; ok && cur == wp {
			delete(r.windows, id)
		}
		r.mu.Unlock()
		// stdin 关掉 (双关无害) — readLoop 也会因 stdout EOF 自然退.
		_ = stdin.Close()
	}()

	return wp, nil
}

// readLoop: 读 loomer stdout 的 NDJSON. result msg → 路由 pending channel.
// EOF / 解析错 → 静默退出 (reaper 已清表). 其他 type (ready 等) 当前忽略.
func (r *Registry) readLoop(wp *WindowProc, stdout io.ReadCloser) {
	defer stdout.Close()
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024) // 容 4MB handler output
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var msg protocol.LoomerMsg
		if err := json.Unmarshal(line, &msg); err != nil {
			// loomer 输出脏数据 (e.g. stderr 漏到 stdout) — 静默丢, 防雪崩
			continue
		}
		switch msg.Type {
		case "ready":
			// 启动握手, 携带 control HTTP port (loopback). 存进 wp 让 list 透传给
			// agent. 0 表示 loomer 没起 control server (旧版不带), 兼容.
			if msg.ControlPort > 0 {
				wp.controlPort.Store(int32(msg.ControlPort))
			}
		case "result":
			if msg.RequestID == "" {
				continue
			}
			r.deliverResult(msg.RequestID, msg.Output, msg.Error)
		}
	}
}

func (r *Registry) deliverResult(requestID string, output json.RawMessage, errStr string) {
	r.pendMu.Lock()
	pi, ok := r.pending[requestID]
	if ok {
		delete(r.pending, requestID)
	}
	r.pendMu.Unlock()
	if !ok {
		return // 超时被 Invoke 端清掉的; loomer 后发的 result 视为孤儿丢弃
	}
	if errStr != "" {
		pi.resultCh <- invokeOutcome{err: fmt.Errorf("%s", errStr)}
		return
	}
	pi.resultCh <- invokeOutcome{output: output}
}

// Invoke: 给已开窗推 invocation, 等 handler 返结果 (或 timeout).
// 同 (app, windowName) 多个 window 时, 取找到的第一个 (重复 open 是异常 case,
// 路由策略明确文档化).
//
// timeoutMs <= 0 → defaultInvokeTimeout.
// 超时返 timeout error, 不 kill loomer (window 仍活, 用户能继续交互).
func (r *Registry) Invoke(
	app, windowName, invocationID string,
	args json.RawMessage,
	timeoutMs int,
) (json.RawMessage, error) {
	wp := r.findByName(app, windowName)
	if wp == nil {
		return nil, fmt.Errorf("no window with app=%s window_name=%s (用户先点 Open Window?)", app, windowName)
	}

	requestID := newRequestID()
	pi := &pendingInvocation{resultCh: make(chan invokeOutcome, 1)}
	r.pendMu.Lock()
	r.pending[requestID] = pi
	r.pendMu.Unlock()

	// 释放 pending — 无论成功 / 超时 / 错都清掉, deliverResult 后到的孤儿会自然丢
	defer func() {
		r.pendMu.Lock()
		delete(r.pending, requestID)
		r.pendMu.Unlock()
	}()

	// 串行化 stdin 写 — 跨 goroutine 同时写 NDJSON 会撕字节
	wp.stdinMu.Lock()
	msg := protocol.LoomerMsg{
		Type:         "invoke",
		RequestID:    requestID,
		InvocationID: invocationID,
		Args:         args,
	}
	payload, err := json.Marshal(msg)
	if err != nil {
		wp.stdinMu.Unlock()
		return nil, fmt.Errorf("marshal invoke msg: %w", err)
	}
	payload = append(payload, '\n')
	_, writeErr := wp.stdin.Write(payload)
	wp.stdinMu.Unlock()
	if writeErr != nil {
		return nil, fmt.Errorf("loomer stdin write: %w (loomer 死了?)", writeErr)
	}

	timeout := defaultInvokeTimeout
	if timeoutMs > 0 {
		timeout = time.Duration(timeoutMs) * time.Millisecond
	}

	select {
	case outcome := <-pi.resultCh:
		if outcome.err != nil {
			return nil, outcome.err
		}
		return outcome.output, nil
	case <-time.After(timeout):
		return nil, fmt.Errorf("invoke timeout after %s (window handler 卡了, 但 window 仍活)", timeout)
	}
}

func (r *Registry) findByName(app, windowName string) *WindowProc {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, wp := range r.windows {
		if wp.App == app && wp.WindowName == windowName {
			return wp
		}
	}
	return nil
}

// Close: 杀指定 window 的 loomer 子进程. 如果 id 不存在或已退, 返 not found 错.
// Kill 后 reaper goroutine 会自动清表, 这里不主动 delete.
func (r *Registry) Close(id string) error {
	r.mu.Lock()
	wp, ok := r.windows[id]
	r.mu.Unlock()
	if !ok {
		return fmt.Errorf("window %s not found", id)
	}
	if err := wp.cmd.Process.Kill(); err != nil {
		// Kill 错 (e.g. 进程已 exit) 也认为成功 — 用户的目标是"窗没了", 已经达成.
		_ = err
	}
	return nil
}

// CloseByName: 按 (app, window_name) 二级索引找窗杀掉.
// 多个匹配时全杀 (重复 open 同一 window 是异常 case, 全清更安全).
// 返杀掉的数量; 0 表示没找到.
func (r *Registry) CloseByName(app, windowName string) int {
	r.mu.Lock()
	matches := make([]*WindowProc, 0)
	for _, wp := range r.windows {
		if wp.App == app && wp.WindowName == windowName {
			matches = append(matches, wp)
		}
	}
	r.mu.Unlock()
	for _, wp := range matches {
		_ = wp.cmd.Process.Kill()
	}
	return len(matches)
}

// List: 当前活着的 windows snapshot. appFilter="" 时返全部.
func (r *Registry) List(appFilter string) []protocol.WindowInfo {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]protocol.WindowInfo, 0, len(r.windows))
	for _, wp := range r.windows {
		if appFilter != "" && wp.App != appFilter {
			continue
		}
		out = append(out, protocol.WindowInfo{
			WindowID:    wp.ID,
			PID:         wp.PID,
			EntryPath:   wp.EntryPath,
			Title:       wp.Title,
			App:         wp.App,
			WindowName:  wp.WindowName,
			StartedAt:   wp.StartedAt.Unix(),
			ControlPort: int(wp.controlPort.Load()),
		})
	}
	return out
}

// KillAll: daemon 退出时调, 杀掉所有 loomer 子进程. 防用户重启 daemon 时残留窗.
func (r *Registry) KillAll() {
	r.mu.Lock()
	procs := make([]*exec.Cmd, 0, len(r.windows))
	for _, wp := range r.windows {
		procs = append(procs, wp.cmd)
	}
	r.mu.Unlock()
	for _, c := range procs {
		_ = c.Process.Kill()
	}
}

// newWindowID: 6 字节 hex (12 字符), 够防碰撞同时短到 CLI 可读手敲.
func newWindowID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// newRequestID: 8 字节 hex (16 字符). 用比 windowID 长一点防 in-flight 多 req 时
// 碰撞 (concurrent invoke 多, 但 window 总数低).
func newRequestID() string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}
