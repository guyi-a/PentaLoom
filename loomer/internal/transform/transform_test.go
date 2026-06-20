package transform

import (
	"os"
	"path/filepath"
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
	// "Hello from PentaLoom loomer" 在 H1 里跑 runtime, treeshake 不掉.
	// (windowConfig 这种 export const 没人引用会被 wrapper 模式下 treeshake)
	for _, expect := range []string{"fmtKB", "Card", "Hello from PentaLoom loomer"} {
		if !strings.Contains(js, expect) {
			t.Errorf("bundle missing %q (multi-file resolve broken)", expect)
		}
	}

	// react / react/jsx-runtime 应该被 OnResolve 改写成完整 esm.sh URL,
	// 不被打进 bundle. webview 端直接 fetch URL, 不依赖 importmap.
	for _, ext := range []string{
		`from "https://esm.sh/react@18.3.1"`,
		`from "https://esm.sh/react@18.3.1/jsx-runtime"`,
	} {
		if !strings.Contains(js, ext) {
			t.Errorf("expected rewritten URL import %q in bundle", ext)
		}
	}
	// react 自己的源码不应该出现 (那意味着没 external 而是 bundle 进去了).
	if strings.Contains(js, "createReactClass") || strings.Contains(js, "react-dom/client.js") {
		t.Errorf("react/react-dom appears bundled (should be external via esm.sh URL)")
	}
}

// TestBundleHelloDesignApp: lucide-react / radix-ui / @/lib/utils 都在白名单内,
// 应被 OnResolve 改写成 https://esm.sh/X (或 data:URL 给 cn helper), 不打进 bundle.
func TestBundleHelloDesignApp(t *testing.T) {
	js, err := Bundle("../../testdata/hello-design-app/index.tsx")
	if err != nil {
		t.Fatalf("Bundle failed: %v", err)
	}

	// bundle 里 import 是改写后的 URL (esm.sh 或 data:).
	for _, ext := range []string{
		`from "https://esm.sh/lucide-react@0.300.0?deps=react@18.3.1,react-dom@18.3.1"`,
		`from "https://esm.sh/radix-ui?bundle&deps=react@18.3.1,react-dom@18.3.1"`,
		`from "data:application/javascript;base64,`, // @/lib/utils → cn helper inline
	} {
		if !strings.Contains(js, ext) {
			t.Errorf("expected rewritten URL import %q in bundle", ext)
		}
	}
	// 原 bare specifier 不应再出现在 from 子句里.
	for _, bare := range []string{
		`from "lucide-react"`,
		`from "radix-ui"`,
		`from "@/lib/utils"`,
	} {
		if strings.Contains(js, bare) {
			t.Errorf("bundle still has bare specifier %q (OnResolve rewrite not applied)", bare)
		}
	}

	// arbitrary value Tailwind class 字面量应该被 inline (Tailwind CDN 在 webview
	// 端编译, 这里只是验证 className 字符串原样进 bundle).
	if !strings.Contains(js, "bg-[#08090a]") || !strings.Contains(js, "tracking-[-0.022em]") {
		t.Errorf("arbitrary value Tailwind class not preserved in bundle")
	}
}

// TestBundleRejectsNonWhitelistedPackage: import 非白名单 bare specifier 必须报清晰错.
func TestBundleRejectsNonWhitelistedPackage(t *testing.T) {
	tmp := t.TempDir()
	entry := filepath.Join(tmp, "bad.tsx")
	src := `import axios from "axios";
export default function App() { return <div>{String(axios)}</div>; }
`
	if err := os.WriteFile(entry, []byte(src), 0644); err != nil {
		t.Fatalf("write tmp entry: %v", err)
	}

	_, err := Bundle(entry)
	if err == nil {
		t.Fatalf("expected error for non-whitelisted package, got nil")
	}
	msg := err.Error()
	for _, want := range []string{`"axios"`, "esm.sh"} {
		if !strings.Contains(msg, want) {
			t.Errorf("error message missing %q; got: %s", want, msg)
		}
	}
}
