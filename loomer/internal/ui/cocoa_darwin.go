//go:build darwin

// Cocoa bridge — webview_go 没暴露 NSWindow miniaturize / zoom 等 macOS 原生
// 窗口操作, 这里走 cgo 拿底层 NSWindow* 指针调 selector.
//
// w.Window() 返 unsafe.Pointer, 在 macOS 上即 NSWindow*. 走 uintptr 中转给
// cgo (Go cgo 规则: 不允许把 Go 指针直接塞 C 函数, 但 NSWindow* 是 Cocoa
// 自己分配的不算 Go 指针, 转 uintptr 安全).
package ui

/*
#cgo CFLAGS: -x objective-c
#cgo LDFLAGS: -framework Cocoa -framework WebKit

#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

void loomer_miniaturize(uintptr_t nsWindowPtr);
void loomer_zoom(uintptr_t nsWindowPtr);

// floating widget 4 件套 setter (跟 windowConfig 字段对位).
void loomer_set_titlebar_hidden(uintptr_t nsWindowPtr);
void loomer_set_transparent(uintptr_t nsWindowPtr);
void loomer_set_always_on_top(uintptr_t nsWindowPtr);
void loomer_set_movable_by_background(uintptr_t nsWindowPtr);

// JS mousedown 监听调 ipc begin-drag 时, native 同步走 NSWindow performDrag.
void loomer_perform_window_drag(uintptr_t nsWindowPtr);

// loomer_screenshot: 截 webview 内容区为 PNG bytes.
// 走 WKWebView takeSnapshotWithConfiguration: (macOS 11+, sync wait via semaphore).
// 不需要 Screen Recording 权限 (截自己 webview 内容, 不走屏幕捕获路径).
// 返 (data, len) 由调用方 free; *out_len=0 表示失败.
// errBuf 给错误描述 (cap-1 size, 0 终止), 不要传 NULL.
unsigned char *loomer_screenshot(uintptr_t nsWindowPtr, size_t *out_len,
                                 char *errBuf, size_t errBufCap);
*/
import "C"
import (
	"fmt"
	"unsafe"
)

// miniaturize: 把窗口最小化到 dock (跟 macOS 黄圆点一样).
func miniaturize(nsWindow unsafe.Pointer) {
	if nsWindow == nil {
		return
	}
	C.loomer_miniaturize(C.uintptr_t(uintptr(nsWindow)))
}

// zoom: 切换 zoom 状态 (跟 macOS 绿圆点一样, 切窗口 maximum useful size).
func zoom(nsWindow unsafe.Pointer) {
	if nsWindow == nil {
		return
	}
	C.loomer_zoom(C.uintptr_t(uintptr(nsWindow)))
}

// setTitlebarHidden: NSWindowStyleMaskBorderless — titlebar 整个没 (含圆点),
// 内容延伸到顶部. close / drag 全靠 TSX + window.ipc / movable.
func setTitlebarHidden(nsWindow unsafe.Pointer) {
	if nsWindow == nil {
		return
	}
	C.loomer_set_titlebar_hidden(C.uintptr_t(uintptr(nsWindow)))
}

// setTransparent: 窗 + WKWebView 都不画背景. 实际形状由 TSX body / 卡片 div 决定.
func setTransparent(nsWindow unsafe.Pointer) {
	if nsWindow == nil {
		return
	}
	C.loomer_set_transparent(C.uintptr_t(uintptr(nsWindow)))
}

// setAlwaysOnTop: NSFloatingWindowLevel — 普通 app 切前台不会压住挂件.
func setAlwaysOnTop(nsWindow unsafe.Pointer) {
	if nsWindow == nil {
		return
	}
	C.loomer_set_always_on_top(C.uintptr_t(uintptr(nsWindow)))
}

// setMovableByBackground: 任意背景点击拖窗 — 给 borderless / 透明窗用,
// 普通 app 默认 false 因为 titlebar 已能拖.
func setMovableByBackground(nsWindow unsafe.Pointer) {
	if nsWindow == nil {
		return
	}
	C.loomer_set_movable_by_background(C.uintptr_t(uintptr(nsWindow)))
}

// performWindowDrag: JS mousedown 监听器调 ipc 'begin-drag' 触发的 native 路径.
// 同步调 macOS performWindowDragWithEvent: 让 OS 接管, mouseup 自动结束.
// 必须在 webview Bind callback 主线程同步路径下调 (否则 NSApp.currentEvent 已变).
func performWindowDrag(nsWindow unsafe.Pointer) {
	if nsWindow == nil {
		return
	}
	C.loomer_perform_window_drag(C.uintptr_t(uintptr(nsWindow)))
}

// screenshot: 截 webview 内容区为 PNG bytes. 走 CGWindowListCreateImage +
// ImageIO PNG 编码, 不需要 Screen Recording 权限 (截自己进程窗子).
// nsWindow 是空指针返 (nil, error).
func screenshot(nsWindow unsafe.Pointer) ([]byte, error) {
	if nsWindow == nil {
		return nil, fmt.Errorf("screenshot: nsWindow is nil")
	}
	const errBufCap = 256
	errBuf := (*C.char)(C.calloc(1, errBufCap))
	defer C.free(unsafe.Pointer(errBuf))

	var outLen C.size_t
	dataPtr := C.loomer_screenshot(
		C.uintptr_t(uintptr(nsWindow)),
		&outLen,
		errBuf, C.size_t(errBufCap),
	)
	if dataPtr == nil || outLen == 0 {
		errMsg := C.GoString(errBuf)
		if errMsg == "" {
			errMsg = "loomer_screenshot returned empty (unknown error)"
		}
		return nil, fmt.Errorf("screenshot: %s", errMsg)
	}
	defer C.free(unsafe.Pointer(dataPtr))
	// 拷贝出 Go-managed slice (C buffer 上面 defer free).
	out := C.GoBytes(unsafe.Pointer(dataPtr), C.int(outLen))
	return out, nil
}
