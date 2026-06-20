//go:build !darwin

// non-darwin stub — minimize / maximize / screenshot 在 Linux / Windows
// 暂不支持 (PentaLoom 当前只测过 macOS, loomer 跟 webview_go 在别的平台跑得起来但
// NSWindow / CGWindow API 走不通). stub 让 build 通过.

package ui

import (
	"fmt"
	"unsafe"
)

func miniaturize(nsWindow unsafe.Pointer) {} // no-op on non-darwin
func zoom(nsWindow unsafe.Pointer)         {} // no-op on non-darwin

func screenshot(nsWindow unsafe.Pointer) ([]byte, error) {
	return nil, fmt.Errorf("screenshot: not supported on non-darwin platform")
}
