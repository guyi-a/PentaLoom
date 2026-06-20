package ui

import (
	"encoding/base64"
	"strings"
	"testing"
)

// TestResolveSpecifier_HasAllAllowed: AllowedBareSpecifiers 全部能 resolve 到非空 URL.
func TestResolveSpecifier_HasAllAllowed(t *testing.T) {
	for _, spec := range AllowedBareSpecifiers {
		url := ResolveSpecifier(spec)
		if url == "" {
			t.Errorf("ResolveSpecifier(%q) returned empty (not in mapping?)", spec)
		}
	}
}

// TestResolveSpecifier_UnknownReturnsEmpty: 白名单外的包返空 (transform OnResolve
// 拿到空就抛清晰错).
func TestResolveSpecifier_UnknownReturnsEmpty(t *testing.T) {
	if url := ResolveSpecifier("axios"); url != "" {
		t.Errorf("expected empty for unknown specifier, got %q", url)
	}
}

// TestCnHelperDataURL: @/lib/utils 必须 decode 出 cn() 函数源码.
func TestCnHelperDataURL(t *testing.T) {
	url := cnHelperDataURL()
	prefix := "data:application/javascript;base64,"
	if !strings.HasPrefix(url, prefix) {
		t.Fatalf("cn helper data URL bad prefix: %s", url[:min(len(url), 60)])
	}
	decoded, err := base64.StdEncoding.DecodeString(strings.TrimPrefix(url, prefix))
	if err != nil {
		t.Fatalf("base64 decode: %v", err)
	}
	for _, want := range []string{
		`import { clsx }`,
		`import { twMerge }`,
		`export function cn`,
	} {
		if !strings.Contains(string(decoded), want) {
			t.Errorf("cn helper missing %q; got:\n%s", want, decoded)
		}
	}
}

// TestBuildHTML_TailwindAndErrorOverlay: HTML 必须含 Tailwind CDN + 全局错误兜底
// (没了 importmap, 但留 Tailwind + error overlay).
func TestBuildHTML_TailwindAndErrorOverlay(t *testing.T) {
	html := buildHTML(Config{Title: "test"}, "// fake bundle")

	mustContain := []string{
		`<script src="https://cdn.tailwindcss.com/"></script>`,
		`window.addEventListener('error'`,
		`window.addEventListener('unhandledrejection'`,
		`<title>test</title>`,
	}
	for _, want := range mustContain {
		if !strings.Contains(html, want) {
			t.Errorf("HTML missing %q", want)
		}
	}

	// 不应再有 importmap (改用 OnResolve 改写绝对 URL 绕过 WKWebView 的 importmap bug).
	if strings.Contains(html, `<script type="importmap">`) {
		t.Errorf("HTML still has importmap (should be removed in favor of bundle-time URL rewrite)")
	}
}

// TestBuildHTML_WindowIpcBootstrap: window.ipc.postMessage(JSON) channel 必须注入.
// 验证 bootstrap 含必要符号, 让 TSX 写 window.ipc.postMessage(...) 时能命中 host.
func TestBuildHTML_WindowIpcBootstrap(t *testing.T) {
	html := buildHTML(Config{Title: "test"}, "// fake bundle")

	mustContain := []string{
		`window.ipc = {`,
		`postMessage: function (payload)`,
		`window.__loom_ipc(payload)`, // 走 webview.Bind 路径
		// 错误防御: 必须验 payload 是 string (krow 同款契约)
		`expects JSON string`,
	}
	for _, want := range mustContain {
		if !strings.Contains(html, want) {
			t.Errorf("HTML window.ipc bootstrap missing %q", want)
		}
	}

	// window.ipc 不应跟 window.pentaloom 在同一 IIFE 里 (概念分离: ipc=单向命令,
	// pentaloom=双向 RPC). 简单验: window.ipc 出现位置在 window.pentaloom 之后.
	idxPenta := strings.Index(html, "window.pentaloom = window.pentaloom")
	idxIpc := strings.Index(html, "window.ipc = {")
	if idxPenta < 0 || idxIpc < 0 || idxIpc < idxPenta {
		t.Errorf("expected window.ipc bootstrap after window.pentaloom; pentaloom at %d, ipc at %d", idxPenta, idxIpc)
	}
}

// TestBuildHTML_ExternalLinkInterceptor: <a href=https?> 自动转 open-path
// (避免 webview 内 navigate 白屏 + 跟用户预期一致 — 外链去系统浏览器).
func TestBuildHTML_ExternalLinkInterceptor(t *testing.T) {
	html := buildHTML(Config{Title: "test"}, "// fake bundle")

	mustContain := []string{
		`document.addEventListener('click'`,         // 拦 click 阶段
		`closest ? e.target.closest('a')`,           // 找 <a> 祖先
		`(https?:|mailto:|file:)`,                   // protocol 白名单
		`type: 'open-path'`,                         // 路由到 ipc
		`{ type: 'open-path', path: href }`,         // payload shape
	}
	for _, want := range mustContain {
		if !strings.Contains(html, want) {
			t.Errorf("HTML external link interceptor missing %q", want)
		}
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
