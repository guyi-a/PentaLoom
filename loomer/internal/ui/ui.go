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
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"sync"

	"github.com/guyi-a/PentaLoom/loomer/internal/logbuf"
	webview "github.com/webview/webview_go"
)

// cnHelperSource — @/lib/utils 内联导出的 cn() helper, shadcn 风组件抄起来直接用.
// 通过 data: URL ESM module 暴露 (transform OnResolve 把 user bundle 里的
// `import { cn } from '@/lib/utils'` 改写到这个 data URL).
//
// 关键: data URL module 不走 esbuild OnResolve, 它的 import 解析在 webview
// 端原样跑 — 所以这里的 import 也必须是完整 URL, 不能写成 bare `"clsx"`.
const cnHelperSource = `import { clsx } from "https://esm.sh/clsx";
import { twMerge } from "https://esm.sh/tailwind-merge";
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
`

// cnHelperDataURL: 把 cnHelperSource 编成 data:application/javascript;base64,...
// transform OnResolve 把 user bundle 里 `@/lib/utils` 改写到这个 URL.
func cnHelperDataURL() string {
	return "data:application/javascript;base64," + base64.StdEncoding.EncodeToString([]byte(cnHelperSource))
}

// specifierURLs — bare specifier → 实际 URL 的映射. esbuild OnResolve 用它把
// bundle 里的 `import { Cog } from "lucide-react"` 改写成完整 URL, 浏览器直接
// fetch, 不走 importmap (WKWebView 的 importmap 对 data: URL entry 跟多 entry
// 混用有 bug, 改成绝对 URL 绕过).
//
// 关键: 所有 React 依赖型包必须加 ?deps=react@18.3.1,react-dom@18.3.1 锁版本.
// 否则 esm.sh 输出的 transitive import 是 `react@^16.8 || ^17.0 || ^18.0 || ...`
// (含空格跟 `||` 字符), WKWebView 的 URL parser 把这种 specifier 当 invalid URL
// 拒绝, 整个 module 加载失败 → 白屏 (radix-ui 实测确认). 锁版本后输出形如
// /react@18.3.1/es2022/react.mjs, 干净.
//
// react family pin 18.3.1: SharedInternals 对齐 (跨包混版本会 hooks 报错).
// lucide-react pin 0.300.0: npm latest 是 1.21.0 老废包 (2018 年, 没现代 icon);
// 0.300.0 是兼容 react@^18 的较晚版本 (0.350+ 起改要 react@19).
// radix-ui ?bundle: 默认聚合包 side-effect import 30+ sub-package 任一失败整体
// 白屏 (WKWebView 实测), ?bundle 让 esm.sh 把所有 primitives 打成单 mjs.
const reactDeps = "?deps=react@18.3.1,react-dom@18.3.1"

var specifierURLs = map[string]string{
	"react":                    "https://esm.sh/react@18.3.1",
	"react/jsx-runtime":        "https://esm.sh/react@18.3.1/jsx-runtime",
	"react-dom":                "https://esm.sh/react-dom@18.3.1",
	"react-dom/client":         "https://esm.sh/react-dom@18.3.1/client",
	"radix-ui":                 "https://esm.sh/radix-ui?bundle&deps=react@18.3.1,react-dom@18.3.1",
	"lucide-react":             "https://esm.sh/lucide-react@0.300.0" + reactDeps,
	"react-markdown":           "https://esm.sh/react-markdown" + reactDeps,
	"remark-gfm":               "https://esm.sh/remark-gfm", // 不依赖 react
	"class-variance-authority": "https://esm.sh/class-variance-authority",
	"clsx":                     "https://esm.sh/clsx",
	"tailwind-merge":           "https://esm.sh/tailwind-merge",
	// "@/lib/utils" 在 ResolveSpecifier 里特殊处理: 走 data URL 内联 cn helper.
}

// AllowedBareSpecifiers — esbuild external 白名单 + 错误信息引用. 任何 bundle
// 里的 bare import 必须在这里, 否则 transform 阶段报清晰错.
var AllowedBareSpecifiers = func() []string {
	out := make([]string, 0, len(specifierURLs)+1)
	for k := range specifierURLs {
		out = append(out, k)
	}
	out = append(out, "@/lib/utils")
	return out
}()

// ResolveSpecifier: bare specifier → 完整 URL. 不在白名单返空串.
// `@/lib/utils` 走 cnHelperDataURL() (动态生成 base64 data URL).
func ResolveSpecifier(spec string) string {
	if spec == "@/lib/utils" {
		return cnHelperDataURL()
	}
	return specifierURLs[spec]
}

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

	// LogRing: window 内 console.{log,warn,error,info,debug} hook 后的累积处.
	// nil 表示不接 (console 调用走默认 webview 行为, 不留底). main 通常创建一个
	// 给 ui + control HTTP server 共用.
	LogRing *logbuf.Ring

	// ScreenshotProvider: control HTTP /screenshot 用. main 起 control server
	// 时拿到这个 provider 注册回调, ui.Run 起 webview 后调 setWindow 注入
	// NSWindow*. nil 表示不支持截图 (跨平台 / 测试场景).
	ScreenshotProvider *ScreenshotProvider
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

	// window.ipc.postMessage(JSON) 路由器 — window→host 单向命令 channel
	// (跟 window.pentaloom.registerInvocation 双向 RPC 并存, 各管各的).
	// payload 必须是 JSON 字符串 (跟 krow 同款), 含 type 字段决定动作.
	w.Bind("__loom_ipc", func(payloadJSON string) any {
		var msg struct {
			Type string `json:"type"`
		}
		if err := json.Unmarshal([]byte(payloadJSON), &msg); err != nil {
			return map[string]any{"ok": false, "error": "invalid JSON: " + err.Error()}
		}
		switch msg.Type {
		case "close":
			// 主线程 Terminate webview run loop, Run() 返回, loomer 进程退出.
			// Dispatch 让动作在主线程跑 (从 JS Bind callback 这条 thread 跳过去).
			w.Dispatch(func() { w.Terminate() })
			return map[string]any{"ok": true}
		case "minimize":
			// cgo NSWindow miniaturize: 收到 macOS dock 的 mini 动画.
			// non-darwin 是 no-op (stub).
			miniaturize(w.Window())
			return map[string]any{"ok": true}
		case "maximize":
			// cgo NSWindow zoom: 切 max useful size <-> 原 size, 跟绿圆点一致.
			zoom(w.Window())
			return map[string]any{"ok": true}
		case "open-path":
			// 打开外部 URL / 本地文件 / 文件夹, 走系统默认 app (open 命令).
			// path 不做白名单 — TSX 是 agent 自己织的, 信任. 非 darwin 平台
			// 走 platform 别的命令 (xdg-open / start), 这里 macOS-first.
			var pmsg struct {
				Path string `json:"path"`
			}
			if err := json.Unmarshal([]byte(payloadJSON), &pmsg); err != nil {
				return map[string]any{"ok": false, "error": "invalid open-path payload: " + err.Error()}
			}
			if pmsg.Path == "" {
				return map[string]any{"ok": false, "error": "open-path requires non-empty path"}
			}
			if err := openPath(pmsg.Path); err != nil {
				return map[string]any{"ok": false, "error": err.Error()}
			}
			return map[string]any{"ok": true}
		default:
			return map[string]any{
				"ok":    false,
				"error": "unknown ipc type: " + msg.Type + " (supported: close, minimize, maximize, open-path)",
			}
		}
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

	// console hook: JS 端 override 原生 console.{log,warn,error,info,debug} →
	// __loom_console(level, args[]) 把 entry 写进 LogRing. control HTTP server
	// 的 /logs endpoint 读 ring 给 agent. ring 没设置 (nil) 就丢弃.
	w.Bind("__loom_console", func(level string, args []string) any {
		if opts.LogRing != nil {
			opts.LogRing.Append(level, args)
		}
		return nil
	})

	w.SetHtml(buildHTML(cfg, opts.BundleJS))

	// 把 NSWindow* 注入 ScreenshotProvider — control HTTP /screenshot 之前
	// 拿到的 closure 现在能解析到 webview 句柄. SetHtml 之后 w.Window() 已 valid.
	if opts.ScreenshotProvider != nil {
		opts.ScreenshotProvider.setWindow(w.Window())
	}

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
	// 内联 bundle 到 <script type="module">. bundle 里的 bare specifier 已经在
	// esbuild OnResolve 阶段被改写成完整 https:// URL 或 data:URL, 所以这里
	// 不需要 importmap (WKWebView 的 importmap 实现对多 entry / data URL entry
	// 有 bug, 改写成绝对 URL 绕过).
	// Tailwind v3 CDN 实时编译 design-* 里的 arbitrary value (bg-[#0071e3] 等),
	// 必须在 user bundle 加载前先 ready.
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
<script src="https://cdn.tailwindcss.com/"></script>
<script>
// 全局错误兜底: module load failure / promise reject / runtime throw 都
// 显示到 #root, 否则 webview 没 devtools 时就是静默白屏.
(function () {
  function showError(prefix, e) {
    const root = document.getElementById('root');
    if (!root) return;
    const msg = (e && (e.stack || e.message)) || String(e);
    const safe = msg.replace(/[<>&]/g, function (c) {
      return c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&amp;';
    });
    root.innerHTML =
      '<pre style="padding:24px;color:#b22222;white-space:pre-wrap;font-family:ui-monospace,monospace;font-size:12px;">' +
      'loomer ' + prefix + ':\n\n' + safe + '</pre>';
  }
  window.addEventListener('error', function (ev) {
    showError(ev.filename ? 'script error (' + ev.filename + ')' : 'script error', ev.error || ev.message);
  });
  window.addEventListener('unhandledrejection', function (ev) {
    showError('unhandled promise rejection', ev.reason);
  });
})();
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
<script>
// window.ipc.postMessage(JSON) — window→host 单向命令 channel.
// 跟 krow 同款 idiom: window 内 TSX 调 window.ipc.postMessage(JSON.stringify({type:'close'}))
// 让 host 关闭窗口 / 最小化 / 打开外链 等. 跟 window.pentaloom 双向 RPC 是不同概念,
// 不要混: pentaloom 是 agent↔window 业务 RPC, ipc 是 window→host 命令.
(function () {
  window.ipc = {
    postMessage: function (payload) {
      if (typeof payload !== 'string') {
        throw new Error('window.ipc.postMessage expects JSON string, got ' + typeof payload);
      }
      // __loom_ipc 是 Go 端 webview.Bind 注册的, 同步返 {ok, error?}.
      // 不抛异常给调用方 (尽量), 由 host 路由器处理 unknown type.
      return window.__loom_ipc(payload);
    }
  };

  // 外链自动拦截: 用户在 window 里点 <a href="https://...">, 默认浏览器会
  // 试图在 webview 内 navigate, white-screen 风险 + 用户期望是去系统浏览器.
  // 这里 capture 阶段拦所有 click, 命中外部 URL 就 preventDefault + 走 open-path.
  // target=_blank 也走这条 (本来就不该让 webview 开新窗).
  document.addEventListener('click', function (e) {
    var a = e.target && (e.target.closest ? e.target.closest('a') : null);
    if (!a) return;
    var href = a.getAttribute('href') || '';
    // 只拦 http/https 跟 file:// / mailto:; webview 内 # / / 锚点 SPA 路由不动.
    if (/^(https?:|mailto:|file:)/i.test(href)) {
      e.preventDefault();
      try {
        window.ipc.postMessage(JSON.stringify({ type: 'open-path', path: href }));
      } catch (err) {
        // 这里不 console.error 防止日志环回 (console 已 hook, 错信息会被吃掉);
        // 真要 debug 看 webview devtools 或者改 console.warn 走原 native 路径.
      }
    }
  }, true);

  // console hook: 重写原生 console.{log,warn,error,info,debug} 让 host 累积日志,
  // agent 调 app_window_logs 时能拿到. 同时仍走原 native console (devtools 看得到).
  // args 序列化用 JSON.stringify, 失败 (循环引用 / DOM 节点) fallback String().
  var nativeConsole = {};
  ['log', 'warn', 'error', 'info', 'debug'].forEach(function (level) {
    nativeConsole[level] = console[level] && console[level].bind(console);
    console[level] = function () {
      var args = [];
      for (var i = 0; i < arguments.length; i++) {
        var a = arguments[i];
        try {
          args.push(typeof a === 'string' ? a : JSON.stringify(a));
        } catch (e) {
          args.push(String(a));
        }
      }
      try {
        window.__loom_console(level, args);
      } catch (e) { /* host 不 ready 也不 break */ }
      if (nativeConsole[level]) {
        nativeConsole[level].apply(console, arguments);
      }
    };
  });
})();
</script>
</head>
<body>
<div id="root"></div>
<script type="module">
%s
</script>
</body>
</html>`
	// `</script>` 在 HTML 里会提早终止 script tag — 反斜杠化 / 让 HTML parser
	// 看不到 close tag, JS 端 `<\/script>` 跟 `</script>` 等价 (反斜杠在 / 前 noop).
	safeBundle := strings.ReplaceAll(bundleJS, "</script>", "<\\/script>")
	return fmt.Sprintf(tpl, escapeHTML(cfg.Title), safeBundle)
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

