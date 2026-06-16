// Package transform 把 entry .tsx + 相对 import 的依赖文件 bundle 成单段 ESM JS,
// 给 webview 内联加载. react / react/jsx-runtime 等 bare specifier 标 external,
// 留 webview 端 importmap 走 esm.sh.
//
// 实现走 esbuild Go API + plugin onResolve + onLoad — 拦所有路径自己处理:
//   - bare 名字 (react 等) → External=true, 不打进 bundle
//   - 相对路径 (./components/Card) → 拼到 importer 的目录, 多 ext 探测 (.tsx/.ts/.jsx/.js)
//   - 绝对路径 → 直接读文件系统
//
// 跟 spike 1 (/tmp/loom-spike/esbuild) 同款架构, 但 OnLoad 从 os.ReadFile 而非虚拟 map.
package transform

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/evanw/esbuild/pkg/api"
)

// Bundle 把 entry 文件以及它递归引到的所有相对依赖合并成一段 ESM JS 字符串.
// 返回的 JS 适合直接塞进 webview 的 <script type="module"> 标签.
//
// react / react-dom / react/jsx-runtime 不会被 bundle, 由 webview 端 importmap
// 解析到 esm.sh CDN.
func Bundle(entryPath string) (string, error) {
	abs, err := filepath.Abs(entryPath)
	if err != nil {
		return "", fmt.Errorf("resolve entry path: %w", err)
	}
	if _, err := os.Stat(abs); err != nil {
		return "", fmt.Errorf("entry not found: %w", err)
	}

	result := api.Build(api.BuildOptions{
		EntryPoints:     []string{abs},
		Bundle:          true,
		Write:           false,
		Format:          api.FormatESModule,
		Target:          api.ES2020,
		JSX:             api.JSXAutomatic,
		JSXImportSource: "react",
		// 这些 bare specifier 不打进 bundle, 留 importmap 处理.
		External: []string{"react", "react/jsx-runtime", "react-dom", "react-dom/client"},
		Plugins:  []api.Plugin{loomerPlugin()},
	})

	if len(result.Errors) > 0 {
		return "", formatErrors(result.Errors)
	}
	if len(result.OutputFiles) == 0 {
		return "", fmt.Errorf("esbuild returned no output")
	}
	return string(result.OutputFiles[0].Contents), nil
}

// loomerPlugin 拦所有 import 路径, 自己处理 bare / 相对 / 绝对三种.
func loomerPlugin() api.Plugin {
	return api.Plugin{
		Name: "loomer-fs-resolver",
		Setup: func(build api.PluginBuild) {
			build.OnResolve(api.OnResolveOptions{Filter: ".*"}, func(args api.OnResolveArgs) (api.OnResolveResult, error) {
				// bare specifier (没 . 也没 /) → external, 留给 importmap.
				if !strings.HasPrefix(args.Path, ".") && !strings.HasPrefix(args.Path, "/") {
					return api.OnResolveResult{Path: args.Path, External: true}, nil
				}

				// 相对路径 → 拼到 importer 的目录.
				var resolved string
				if strings.HasPrefix(args.Path, ".") {
					base := filepath.Dir(args.Importer)
					resolved = filepath.Clean(filepath.Join(base, args.Path))
				} else {
					resolved = args.Path
				}

				// 探测多种扩展名: .tsx > .ts > .jsx > .js
				if hit := probePath(resolved); hit != "" {
					return api.OnResolveResult{Path: hit, Namespace: "loomer-fs"}, nil
				}
				return api.OnResolveResult{}, fmt.Errorf("not found: %s (from %s)", resolved, args.Importer)
			})

			build.OnLoad(api.OnLoadOptions{Filter: ".*", Namespace: "loomer-fs"}, func(args api.OnLoadArgs) (api.OnLoadResult, error) {
				content, err := os.ReadFile(args.Path)
				if err != nil {
					return api.OnLoadResult{}, fmt.Errorf("read %s: %w", args.Path, err)
				}
				s := string(content)
				return api.OnLoadResult{
					Contents: &s,
					Loader:   loaderFor(args.Path),
				}, nil
			})
		},
	}
}

// probePath: 用户写 ./components/Card → 探 ./components/Card.tsx 等扩展.
// 已带 ext (e.g. ./components/Card.tsx) 直接返.
func probePath(p string) string {
	if _, err := os.Stat(p); err == nil {
		return p
	}
	for _, ext := range []string{".tsx", ".ts", ".jsx", ".js"} {
		candidate := p + ext
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	// 目录: ./components → ./components/index.tsx 等
	for _, idx := range []string{"/index.tsx", "/index.ts", "/index.jsx", "/index.js"} {
		candidate := p + idx
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
	}
	return ""
}

func loaderFor(path string) api.Loader {
	switch filepath.Ext(path) {
	case ".tsx":
		return api.LoaderTSX
	case ".ts":
		return api.LoaderTS
	case ".jsx":
		return api.LoaderJSX
	case ".js":
		return api.LoaderJS
	default:
		return api.LoaderJS
	}
}

func formatErrors(errs []api.Message) error {
	var b strings.Builder
	b.WriteString("transform errors:\n")
	for _, e := range errs {
		if e.Location != nil {
			fmt.Fprintf(&b, "  %s:%d:%d: %s\n", e.Location.File, e.Location.Line, e.Location.Column, e.Text)
		} else {
			fmt.Fprintf(&b, "  %s\n", e.Text)
		}
	}
	return fmt.Errorf("%s", b.String())
}
