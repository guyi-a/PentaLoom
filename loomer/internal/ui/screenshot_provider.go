// ScreenshotProvider — 让 main.go 在 ui.Run 起 webview 之前就拿到一个稳定的
// 截图函数引用, ui.Run 起来后再注入真实 NSWindow* 句柄.
//
// 为什么不直接传函数: control HTTP server 在 ready msg 之前就要起 (这样 ready
// 才能带 control_port), 但 webview NSWindow* 在 ui.Run 之后才存在. provider 模式
// 让 control server 拿到一个"未填值的截图函数", ui.Run 起来 set NSWindow*,
// HTTP 请求来时 closure 看到已 set 就执行, 否则返 not-ready 错.

package ui

import (
	"fmt"
	"sync/atomic"
	"unsafe"
)

// ScreenshotProvider 的 NSWindow* 在 ui.Run 起 webview 后才 set. HTTP handler
// 端走 atomic.Pointer 读, 没 set 就返清晰错.
type ScreenshotProvider struct {
	nsWindow atomic.Pointer[unsafe.Pointer]
}

// NewScreenshotProvider 给 main.go 调, 返一个 nsWindow 还没 set 的 provider.
func NewScreenshotProvider() *ScreenshotProvider {
	return &ScreenshotProvider{}
}

// setWindow: ui.Run 内部调, 把 webview NSWindow* 注入. 调一次.
func (p *ScreenshotProvider) setWindow(nsWindow unsafe.Pointer) {
	if nsWindow == nil {
		return
	}
	p.nsWindow.Store(&nsWindow)
}

// HTTPHandler: 给 control.New 当 ScreenshotFunc 用. 没 set window 返清晰错.
// set 之后调 cgo screenshot() 走 WKWebView takeSnapshot 路径.
func (p *ScreenshotProvider) HTTPHandler() ([]byte, error) {
	ptr := p.nsWindow.Load()
	if ptr == nil {
		return nil, fmt.Errorf("screenshot: webview not yet ready (ui.Run 还没 set NSWindow); 等 1-2s 重试")
	}
	return screenshot(*ptr)
}
