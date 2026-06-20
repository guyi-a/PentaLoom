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

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
