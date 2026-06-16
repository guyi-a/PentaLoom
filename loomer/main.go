// loomer — 单窗 host 进程. loom daemon spawn 出来给一个 weaver invocable app
// 渲 window 用. 接 CLI flags, 启 webview, 加载 esbuild bundle 出来的 React app.
//
// 用法:
//   loomer --entry /path/to/index.tsx [--width 800] [--height 600] [--title "App"]
//
// 设计原则: 单窗一进程, 一关窗一进程退出. loom 通过 PID 监管 + spawn 新进程开新窗.
//
// stdio 协议 (双向 NDJSON):
//   stdin  loom → loomer:   {"type":"invoke", "request_id":..., "invocation_id":..., "args":...}
//   stdout loomer → loom:   {"type":"ready"}  (启动握手)
//                           {"type":"result", "request_id":..., "output":...}
//                           {"type":"result", "request_id":..., "error":...}
//
// stdin reader goroutine 把 invoke msg 投到 main 线程 webview.Dispatch 调 JS handler.
// JS handler async 完了通过 webview.Bind 的 __pentaloom_handler_result 回报 Go,
// Go 写一行 result NDJSON 到 stdout, loom 端 readLoop 路由回 pending channel.
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/guyi-a/PentaLoom/loomer/internal/transform"
	"github.com/guyi-a/PentaLoom/loomer/internal/ui"
)

// 协议消息 — 跟 loom/internal/protocol.LoomerMsg 字字对应, 但 loomer 独立 go.mod
// 不引 loom 内部 package (循环依赖), 这里平行 redeclare. 字段加减时同步两边.
type loomerMsg struct {
	Type         string          `json:"type"`
	RequestID    string          `json:"request_id,omitempty"`
	InvocationID string          `json:"invocation_id,omitempty"`
	Args         json.RawMessage `json:"args,omitempty"`
	Output       json.RawMessage `json:"output,omitempty"`
	Error        string          `json:"error,omitempty"`
}

func main() {
	var (
		entry  = flag.String("entry", "", "path to .tsx entry file (required)")
		width  = flag.Int("width", 800, "window width in pixels")
		height = flag.Int("height", 600, "window height in pixels")
		title  = flag.String("title", "", "window title (default: entry filename)")
	)
	flag.Parse()

	if *entry == "" {
		fmt.Fprintln(os.Stderr, "loomer: --entry is required")
		flag.Usage()
		os.Exit(2)
	}

	// title 默认用 entry 文件名 (不含 ext).
	displayTitle := *title
	if displayTitle == "" {
		base := filepath.Base(*entry)
		displayTitle = base[:len(base)-len(filepath.Ext(base))]
	}

	// 1. esbuild 把 entry .tsx + 相对依赖 bundle 成单段 ESM JS.
	bundleJS, err := transform.Bundle(*entry)
	if err != nil {
		fmt.Fprintf(os.Stderr, "loomer: bundle failed: %v\n", err)
		os.Exit(1)
	}

	// 2. 启 webview, 加载 bundle.
	cfg := ui.Config{
		Title:     displayTitle,
		Width:     *width,
		Height:    *height,
		EntryPath: *entry,
	}

	// stdout 写 NDJSON 给 loom (协议出口) — 必须串行化, JS handler 可能并发回报.
	stdoutEnc := newStdoutWriter()

	// "ready" 握手: 提前告诉 loom "我活着, 协议齐了" — 当前 loom 不强制等,
	// 但留出做 health probe 的口子.
	stdoutEnc.send(loomerMsg{Type: "ready"})

	// 给 ui.Run 喂一个 hook: webview 起来后 ui 这边返一个 dispatcher 函数,
	// loomer main 通过它把 stdin 上来的 invoke msg 推到 JS handler.
	// dispatchInvoke 实际签名: func(invocationID string, args json.RawMessage, requestID string)
	uiOpts := ui.Options{
		Config:   cfg,
		BundleJS: bundleJS,
		// __pentaloom_handler_result: JS handler 调来回报 Go.
		OnHandlerResult: func(requestID string, output json.RawMessage, errStr string) {
			msg := loomerMsg{Type: "result", RequestID: requestID}
			if errStr != "" {
				msg.Error = errStr
			} else {
				msg.Output = output
			}
			stdoutEnc.send(msg)
		},
	}

	// 起 stdin reader 之前先建好 webview, 拿到 dispatcher; 但 webview.Run 阻塞,
	// dispatcher 闭包要靠 webview.Dispatch 在主线程跑 JS — 没起 webview 前 Dispatch
	// 无效. 顺序: 起 stdin reader (它先收着 msg 暂存), 起 webview (Run 阻塞主线程,
	// dispatcher ready 后处理积压).
	stdinCh := make(chan loomerMsg, 32)
	go readStdin(stdinCh, stdoutEnc)

	// ui.Run 会回调 OnReady 让 main 注册 dispatcher 用; 等 webview 真起来后
	// 用 webview.Dispatch 串行投递. 不在 OnReady 之前 drain channel — 防 JS 还
	// 没加载完 handler 就被调.
	uiOpts.OnReady = func(dispatcher ui.InvokeDispatcher) {
		// dispatcher 自己内部用 webview.Dispatch 投到主线程, 这里只管喂.
		go func() {
			for msg := range stdinCh {
				if msg.Type == "invoke" {
					dispatcher(msg.InvocationID, msg.Args, msg.RequestID)
				}
			}
		}()
	}

	if err := ui.Run(uiOpts); err != nil {
		fmt.Fprintf(os.Stderr, "loomer: ui run failed: %v\n", err)
		os.Exit(1)
	}
}

// readStdin: 一行一行解析 loom 推过来的 NDJSON, 投进 channel.
// EOF (loom 关 stdin) → 协议正常结束, loomer 进程不退 (window 还可能要用), close channel.
// 解析错 → 报告 loom (stdoutEnc) + 继续读, 防协议小错把整窗拖死.
func readStdin(out chan<- loomerMsg, stdoutEnc *stdoutWriter) {
	defer close(out)
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64*1024), 4*1024*1024) // 容 4MB args
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var msg loomerMsg
		if err := json.Unmarshal(line, &msg); err != nil {
			// 协议错就 log 一行到 loom 端, 不退. loom 看到没 request_id 的 result
			// 当孤儿 drop, 但 stderr 能捞到原因.
			fmt.Fprintf(os.Stderr, "loomer: stdin bad NDJSON: %v\n", err)
			continue
		}
		out <- msg
	}
}

// stdoutWriter: 串行化 stdout 写 — JS handler 多个 result 异步回报, 不加锁会撕字节.
type stdoutWriter struct {
	encoder *json.Encoder
	ch      chan loomerMsg
}

func newStdoutWriter() *stdoutWriter {
	w := &stdoutWriter{
		encoder: json.NewEncoder(os.Stdout),
		ch:      make(chan loomerMsg, 32),
	}
	go w.loop()
	return w
}

func (w *stdoutWriter) send(msg loomerMsg) {
	w.ch <- msg
}

func (w *stdoutWriter) loop() {
	for msg := range w.ch {
		_ = w.encoder.Encode(msg) // Encode 自动加换行, 单 goroutine 串行
	}
}
