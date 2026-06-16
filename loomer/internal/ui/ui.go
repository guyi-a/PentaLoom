// Package ui 包一层 webview_go: 起 WKWebView, 注 importmap + bundle, 暴露双向
// invoke IPC.
//
// HTML 结构:
//   - <head> 内置 importmap (react/react-dom/jsx-runtime → esm.sh CDN)
//   - <body><div id="root"></div>
//   - <script type="module"> 加载 bundle, mount 到 #root, 暴露 window.pentaloom API
//
// 双向 invoke 协议:
//   - JS 注册 handler: window.pentaloom.registerInvocation("greet", async (args) => {...})
//   - Go → JS:  webview.Dispatch + webview.Eval("window.__pentaloom_run_handler(...)")
//   - JS → Go:  webview.Bind("__pentaloom_handler_result", ...) ↑ async handler 返值
//
// 旧的 echo "invokeApp" Bind 保留作 dev demo / 兜底, 不接 loom 协议; 真协议走
// registerInvocation + handler_result.
package ui

import (
	"encoding/json"
	"fmt"
	"sync"

	webview "github.com/webview/webview_go"
)

// Config 描述一个 window 的初始视觉配置.
type Config struct {
	Title  string
	Width  int
	Height int
	// EntryURL 实际不用 — bundle 已经内联进 HTML, 这里留一个 entry path 仅用于
	// debug log (window 标题 fallback / launchctl ps 看进程能识别)
	EntryPath string
}

// InvokeDispatcher: main 拿到这个回调来"给已加载的 JS 推一个 invoke".
// 内部用 webview.Dispatch 投回主线程后 webview.Eval 拼 JS 调用.
// 不等返回 — handler 是 async, 走 OnHandlerResult 回调单独路径回报.
type InvokeDispatcher func(invocationID string, args json.RawMessage, requestID string)

// Options: ui.Run 的输入 — Config + bundle + 两个 IPC 回调.
type Options struct {
	Config   Config
	BundleJS string

	// OnReady: webview 起来 + bundle 已加载, 把 dispatcher 回给 main 用.
	// main 拿到后开始消费 stdin 的 invoke msg.
	OnReady func(dispatcher InvokeDispatcher)

	// OnHandlerResult: JS handler async 完了调 __pentaloom_handler_result(reqID, output, error)
	// 时触发. main 把它打包成 result NDJSON 写 stdout 给 loom.
	OnHandlerResult func(requestID string, output json.RawMessage, errStr string)
}

// Run 起一个窗口 + 阻塞 UI loop. 关窗后函数返回.
func Run(opts Options) error {
	cfg := opts.Config
	w := webview.New(false)
	defer w.Destroy()

	if cfg.Title == "" {
		cfg.Title = "loomer"
	}
	if cfg.Width <= 0 {
		cfg.Width = 800
	}
	if cfg.Height <= 0 {
		cfg.Height = 600
	}
	w.SetTitle(cfg.Title)
	w.SetSize(cfg.Width, cfg.Height, webview.HintNone)

	// 已注册的 invocation id (Go 端记录, 不真用 — 主要是给将来 health probe / debug
	// 看 JS 端有没有调 registerInvocation. 真正路由发生在 JS 内 Map 里).
	var registeredMu sync.Mutex
	registered := make(map[string]struct{})

	w.Bind("__pentaloom_register_invocation", func(id string) any {
		registeredMu.Lock()
		registered[id] = struct{}{}
		registeredMu.Unlock()
		return nil
	})

	// JS handler async 完了通过这个回调把 result / error 投到 Go.
	// args: requestID, output (可 null), error (字符串 / 空)
	w.Bind("__pentaloom_handler_result", func(requestID string, output json.RawMessage, errStr string) any {
		if opts.OnHandlerResult != nil {
			opts.OnHandlerResult(requestID, output, errStr)
		}
		return nil
	})

	// 兼容旧 echo Bind — PR 1 demo 文档里讲的 window.invokeApp 仍能用 (开发态测).
	w.Bind("invokeApp", func(payload json.RawMessage) (any, error) {
		return map[string]any{"echo": string(payload), "note": "legacy echo path"}, nil
	})

	w.SetHtml(buildHTML(cfg, opts.BundleJS))

	// dispatcher: main 用它把 invoke 投到主线程 + Eval 调 JS.
	// 必须在 webview.Run 之前注册回 OnReady — Run 一阻塞就拿不到了.
	// 但 webview.Dispatch 在 Run 之前调实际也不会执行 (没 main loop), 所以这里
	// 直接给 main, main 在 goroutine 里等到 Run 起 dispatch loop 后才会处理.
	dispatcher := func(invocationID string, args json.RawMessage, requestID string) {
		// 主线程才能 Eval — Dispatch 把 closure 投到主线程跑.
		w.Dispatch(func() {
			argsJS := "null"
			if len(args) > 0 {
				argsJS = string(args)
			}
			// JSON 反义不需要 — request_id / invocation_id 是 hex / kebab, 但稳一手用 json.Marshal.
			invIDJSON, _ := json.Marshal(invocationID)
			reqIDJSON, _ := json.Marshal(requestID)
			script := fmt.Sprintf(
				"window.__pentaloom_run_handler(%s, %s, %s)",
				string(reqIDJSON), string(invIDJSON), argsJS,
			)
			w.Eval(script)
		})
	}
	if opts.OnReady != nil {
		opts.OnReady(dispatcher)
	}

	w.Run()
	return nil
}

func buildHTML(cfg Config, bundleJS string) string {
	// 内联 bundle 到 <script type="module">, importmap 把 bare react 引到 esm.sh.
	const tpl = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%s</title>
<style>
  html, body { margin: 0; padding: 0; height: 100%%; }
  body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif; }
  #root { min-height: 100%%; }
</style>
<script type="importmap">
{
  "imports": {
    "react": "https://esm.sh/react@18.3.1",
    "react/jsx-runtime": "https://esm.sh/react@18.3.1/jsx-runtime",
    "react-dom": "https://esm.sh/react-dom@18.3.1",
    "react-dom/client": "https://esm.sh/react-dom@18.3.1/client"
  }
}
</script>
<script>
// pentaloom IPC bootstrap (Go → JS → Go) — 必须在 user bundle 加载前就位,
// 这样 bundle 里立刻调 registerInvocation 也找得到 window.pentaloom.
(function () {
  const handlers = new Map();
  window.pentaloom = window.pentaloom || {};
  window.pentaloom.registerInvocation = function (id, handler) {
    if (typeof handler !== 'function') {
      throw new Error('registerInvocation handler must be function, got ' + typeof handler);
    }
    handlers.set(id, handler);
    // 告知 Go 端 (debug / health probe 用; 不影响路由).
    if (typeof window.__pentaloom_register_invocation === 'function') {
      window.__pentaloom_register_invocation(id);
    }
  };
  window.pentaloom.unregisterInvocation = function (id) {
    handlers.delete(id);
  };
  window.pentaloom._handlers = handlers; // debug 用; 不算公开 API

  // Go → JS: Go 端通过 webview.Eval 调这个. requestID 串回 Go 关联 pending.
  window.__pentaloom_run_handler = async function (requestID, invocationID, args) {
    const handler = handlers.get(invocationID);
    if (!handler) {
      window.__pentaloom_handler_result(
        requestID, null,
        'handler ' + invocationID + ' 未注册 (window.pentaloom.registerInvocation(...))'
      );
      return;
    }
    try {
      const out = await handler(args);
      // 单 arg 还是 多 arg: webview_go Bind 把 (reqID, output, error) 解 3 个参数;
      // output 是任意 JSON value (object/array/primitive), error 是字符串.
      window.__pentaloom_handler_result(requestID, out === undefined ? null : out, '');
    } catch (e) {
      const msg = (e && (e.stack || e.message)) || String(e);
      window.__pentaloom_handler_result(requestID, null, String(msg));
    }
  };
})();
</script>
</head>
<body>
<div id="root"></div>
<script type="module">
import { createRoot } from "react-dom/client";
import { createElement } from "react";

// 把用户 bundle 当一个 module 动态 import. bundle 里 export default Component.
const bundleSource = %s;
const blob = new Blob([bundleSource], { type: "application/javascript" });
const url = URL.createObjectURL(blob);
try {
  const mod = await import(url);
  const App = mod.default;
  if (typeof App !== "function") {
    throw new Error("entry must export default React component (got " + typeof App + ")");
  }
  createRoot(document.getElementById("root")).render(createElement(App));
} catch (e) {
  document.getElementById("root").innerHTML =
    '<pre style="padding:24px;color:#b22222;white-space:pre-wrap;">' +
    'loomer load error:\n\n' + (e && e.stack || String(e)) + '</pre>';
  console.error("[loomer]", e);
}
</script>
</body>
</html>`
	return fmt.Sprintf(tpl, escapeHTML(cfg.Title), jsStringLiteral(bundleJS))
}

// escapeHTML 转义 title 里的 HTML 特殊字符.
func escapeHTML(s string) string {
	out := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		switch s[i] {
		case '&':
			out = append(out, []byte("&amp;")...)
		case '<':
			out = append(out, []byte("&lt;")...)
		case '>':
			out = append(out, []byte("&gt;")...)
		default:
			out = append(out, s[i])
		}
	}
	return string(out)
}

// jsStringLiteral 把任意 string 序列化成合法 JS 字符串字面量.
// 用 json.Marshal — JSON 字符串语法是 JS 字符串子集.
func jsStringLiteral(s string) string {
	b, err := json.Marshal(s)
	if err != nil {
		return `""`
	}
	return string(b)
}
