package transform

import (
	"strings"
	"testing"
)

// TestBundleHelloApp: 把 testdata/hello-app/index.tsx (3 文件多依赖) 打包,
// 验关键 token 都 inline 进 bundle, 同时 react 是 external.
func TestBundleHelloApp(t *testing.T) {
	js, err := Bundle("../../testdata/hello-app/index.tsx")
	if err != nil {
		t.Fatalf("Bundle failed: %v", err)
	}
	if len(js) < 500 {
		t.Errorf("bundle too small: %d bytes (expect 500+)", len(js))
	}

	// 多文件 import 解析: helpers.ts 跟 Card.tsx 应该被 inline 到 bundle.
	for _, expect := range []string{"fmtKB", "Card", "loomer hello"} {
		if !strings.Contains(js, expect) {
			t.Errorf("bundle missing %q (multi-file resolve broken)", expect)
		}
	}

	// react / react/jsx-runtime 应该是 external (importmap 解析), 不被打进来.
	for _, ext := range []string{`from "react"`, `from "react/jsx-runtime"`} {
		if !strings.Contains(js, ext) {
			t.Errorf("expected external import %q in bundle (importmap won't resolve otherwise)", ext)
		}
	}
	// react 自己的源码不应该出现 (那意味着没 external 而是 bundle 进去了).
	if strings.Contains(js, "createReactClass") || strings.Contains(js, "react-dom/client.js") {
		t.Errorf("react/react-dom appears bundled (should be external via importmap)")
	}
}
