// loomctl — weaver app 反向调用 CLI.
//
// schedule / watch / service script 想反向触发 host 能力 (开窗 / 关窗 / 系统通知 /
// 给 PentaLoom 发 invoke / 找 sibling service port) 走它, 而不是自己写 Unix socket
// 协议. binary 跟 loom + loomer 同部署到 ~/.pentaloom/bin/.
//
// 子命令清单:
//
//	loomctl open <app> <window> [--entry path] [--title T] [--width W] [--height H]
//	loomctl close <app> <window>            # 按 (app, window_name) 关
//	loomctl close-id <window-id>            # 按 daemon id 关
//	loomctl status [--app=<app>]            # 列 windows
//	loomctl notify --title=X --body=Y       # 系统通知 (osascript)
//	loomctl invoke <app> <invocation_id> [--args=<json>]   # POST PentaLoom HTTP
//	loomctl service-port <app> <service>    # 输出 .runtime/<svc>.port 数字 (无则 exit 1)
//	loomctl files-dir <app>                 # 输出 ~/.pentaloom/sandboxes/<app>/files
//
// 设计原则: 一条命令做一件事 + 失败 exit 非 0 + stderr 给清楚的人话提示. 避免静默成功.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/guyi-a/PentaLoom/loomctl/internal/loomclient"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	cmd := os.Args[1]
	args := os.Args[2:]

	var err error
	switch cmd {
	case "open":
		err = cmdOpen(args)
	case "close":
		err = cmdClose(args)
	case "close-id":
		err = cmdCloseID(args)
	case "status":
		err = cmdStatus(args)
	case "notify":
		err = cmdNotify(args)
	case "invoke":
		err = cmdInvoke(args)
	case "service-port":
		err = cmdServicePort(args)
	case "files-dir":
		err = cmdFilesDir(args)
	case "-h", "--help", "help":
		usage()
		return
	default:
		fmt.Fprintf(os.Stderr, "loomctl: unknown command %q\n\n", cmd)
		usage()
		os.Exit(2)
	}

	if err != nil {
		fmt.Fprintln(os.Stderr, "loomctl:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprint(os.Stderr, `loomctl — weaver app reverse-call CLI

USAGE:
  loomctl open <app> <window-name> [--entry PATH] [--title T] [--width W] [--height H]
  loomctl close <app> <window-name>
  loomctl close-id <window-id>
  loomctl status [--app APP]
  loomctl notify --title TITLE --body BODY [--app APP]
  loomctl invoke <app> <invocation-id> [--args JSON]
  loomctl service-port <app> <service-name>
  loomctl files-dir <app>

ENV:
  PENTALOOM_HOST       PentaLoom HTTP host (default 127.0.0.1)
  PENTALOOM_PORT       PentaLoom HTTP port (default 8090)

`)
}

// ─── helpers ────────────────────────────────────────────────────────

func newReqID() string {
	var b [4]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

func homeDir() string {
	h, _ := os.UserHomeDir()
	return h
}

// invocable app 物理目录 = <data_dir>/weaver/apps/<app> (跟 paths.app_dir 对齐).
// 不是 sandboxes/ — 那是 chat session sandbox, 跟 invocable app 是两回事.
//
// loomctl 用的优先级:
//  1. weaver_runner 注入的 env (PENTALOOM_APP_DIR / PENTALOOM_FILES_DIR /
//     PENTALOOM_RUNTIME_DIR) — 从 weaver script subprocess 调时永远有这些
//  2. 没 env 时按 PENTALOOM_DATA_DIR 推 (用户从 terminal 手动调 debug 用)
//  3. 都没就拍 ~/.pentaloom (系统级默认; dev 态实际 data_dir 是 repo 路径,
//     那种场景需要 PENTALOOM_DATA_DIR=$PWD/agent/pentaloom-data)

func appDir(app string) string {
	if d := os.Getenv("PENTALOOM_DATA_DIR"); d != "" {
		return filepath.Join(d, "weaver", "apps", app)
	}
	return filepath.Join(homeDir(), ".pentaloom", "weaver", "apps", app)
}

func runtimeDir(app string) string {
	if d := os.Getenv("PENTALOOM_RUNTIME_DIR"); d != "" {
		return d
	}
	return filepath.Join(appDir(app), ".runtime")
}

func filesDirOf(app string) string {
	if d := os.Getenv("PENTALOOM_FILES_DIR"); d != "" {
		return d
	}
	return filepath.Join(appDir(app), "files")
}

func pentaloomURL(path string) string {
	host := os.Getenv("PENTALOOM_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := os.Getenv("PENTALOOM_PORT")
	if port == "" {
		port = "8090"
	}
	return fmt.Sprintf("http://%s:%s%s", host, port, path)
}

// ─── open ───────────────────────────────────────────────────────────

func cmdOpen(args []string) error {
	if len(args) < 2 {
		return fmt.Errorf("open requires <app> <window-name> (got %d args)", len(args))
	}
	app, window := args[0], args[1]

	fs := flag.NewFlagSet("open", flag.ExitOnError)
	entry := fs.String("entry", "", "TSX entry 绝对路径 (省略时从 app.json 读)")
	title := fs.String("title", "", "window title")
	width := fs.Int("width", 0, "window width")
	height := fs.Int("height", 0, "window height")
	_ = fs.Parse(args[2:])

	// entry 省略时从 app.json 解析
	resolvedEntry := *entry
	if resolvedEntry == "" {
		spec, err := readWindowSpec(app, window)
		if err != nil {
			return fmt.Errorf("resolve entry from app.json: %w", err)
		}
		resolvedEntry = filepath.Join(appDir(app), "files", spec.Entry)
		if *title == "" {
			*title = spec.Title
		}
		if *width == 0 {
			*width = spec.Width
		}
		if *height == 0 {
			*height = spec.Height
		}
	}
	abs, err := filepath.Abs(resolvedEntry)
	if err != nil {
		return fmt.Errorf("abs entry: %w", err)
	}
	if _, err := os.Stat(abs); err != nil {
		return fmt.Errorf("entry not found: %s", abs)
	}

	payload, _ := json.Marshal(loomclient.WindowOpenReq{
		EntryPath:  abs,
		Title:      *title,
		Width:      *width,
		Height:     *height,
		App:        app,
		WindowName: window,
	})
	resp, err := loomclient.Send(loomclient.DefaultSocketPath(), &loomclient.Request{
		ID: newReqID(), Cmd: loomclient.CmdWindowOpen, Data: payload,
	})
	if err != nil {
		return err
	}
	if !resp.Ok {
		return fmt.Errorf("loom open failed: %s", resp.Error)
	}
	var r loomclient.WindowOpenResp
	_ = json.Unmarshal(resp.Data, &r)
	fmt.Printf("opened %s/%s window=%s pid=%d\n", app, window, r.WindowID, r.PID)
	return nil
}

// ─── close (by app+name) ────────────────────────────────────────────

func cmdClose(args []string) error {
	if len(args) < 2 {
		return fmt.Errorf("close requires <app> <window-name>")
	}
	app, window := args[0], args[1]
	payload, _ := json.Marshal(loomclient.WindowCloseReq{App: app, WindowName: window})
	resp, err := loomclient.Send(loomclient.DefaultSocketPath(), &loomclient.Request{
		ID: newReqID(), Cmd: loomclient.CmdWindowClose, Data: payload,
	})
	if err != nil {
		return err
	}
	if !resp.Ok {
		return fmt.Errorf("close failed: %s", resp.Error)
	}
	fmt.Printf("closed %s/%s\n", app, window)
	return nil
}

// ─── close-id ───────────────────────────────────────────────────────

func cmdCloseID(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("close-id requires <window-id>")
	}
	id := args[0]
	payload, _ := json.Marshal(loomclient.WindowCloseReq{WindowID: id})
	resp, err := loomclient.Send(loomclient.DefaultSocketPath(), &loomclient.Request{
		ID: newReqID(), Cmd: loomclient.CmdWindowClose, Data: payload,
	})
	if err != nil {
		return err
	}
	if !resp.Ok {
		return fmt.Errorf("close failed: %s", resp.Error)
	}
	fmt.Printf("closed window=%s\n", id)
	return nil
}

// ─── status ─────────────────────────────────────────────────────────

func cmdStatus(args []string) error {
	fs := flag.NewFlagSet("status", flag.ExitOnError)
	app := fs.String("app", "", "filter by app name")
	_ = fs.Parse(args)

	var payload []byte
	if *app != "" {
		payload, _ = json.Marshal(loomclient.WindowListReq{App: *app})
	}
	resp, err := loomclient.Send(loomclient.DefaultSocketPath(), &loomclient.Request{
		ID: newReqID(), Cmd: loomclient.CmdWindowList, Data: payload,
	})
	if err != nil {
		return err
	}
	if !resp.Ok {
		return fmt.Errorf("status failed: %s", resp.Error)
	}
	var r loomclient.WindowListResp
	_ = json.Unmarshal(resp.Data, &r)
	if len(r.Windows) == 0 {
		fmt.Println("(no windows)")
		return nil
	}
	fmt.Printf("%-12s %-6s %-20s %-15s %s\n", "ID", "PID", "APP", "WINDOW", "ENTRY")
	for _, w := range r.Windows {
		fmt.Printf("%-12s %-6d %-20s %-15s %s\n",
			w.WindowID, w.PID,
			truncStr(w.App, 18), truncStr(w.WindowName, 13),
			w.EntryPath)
	}
	return nil
}

// ─── notify (osascript) ─────────────────────────────────────────────

func cmdNotify(args []string) error {
	fs := flag.NewFlagSet("notify", flag.ExitOnError)
	title := fs.String("title", "", "通知标题 (必填)")
	body := fs.String("body", "", "通知正文 (必填)")
	app := fs.String("app", "", "可选: 通知来源 app 名 (展示用)")
	_ = fs.Parse(args)

	if *title == "" || *body == "" {
		return fmt.Errorf("notify --title 和 --body 都必填")
	}
	// macOS 通知中心. 第一次跑会被系统问"是否允许通知".
	// 用 osascript 而不是第三方库 — 零依赖, 跟 launchd 一样是 OS native 兜底.
	subtitle := ""
	if *app != "" {
		subtitle = fmt.Sprintf(`subtitle "%s"`, escapeAS(*app))
	}
	script := fmt.Sprintf(
		`display notification "%s" with title "%s" %s`,
		escapeAS(*body), escapeAS(*title), subtitle,
	)
	if err := exec.Command("osascript", "-e", script).Run(); err != nil {
		return fmt.Errorf("osascript: %w (macOS 通知权限可能没开, 系统设置 > 通知 找 osascript)", err)
	}
	return nil
}

// escapeAS: AppleScript 字符串转义 — 反斜杠 + 双引号.
func escapeAS(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, `"`, `\"`)
	return s
}

// ─── invoke (PentaLoom HTTP) ────────────────────────────────────────

func cmdInvoke(args []string) error {
	if len(args) < 2 {
		return fmt.Errorf("invoke requires <app> <invocation-id>")
	}
	app, invID := args[0], args[1]
	fs := flag.NewFlagSet("invoke", flag.ExitOnError)
	argsJSON := fs.String("args", "{}", "JSON args 给 invocation")
	_ = fs.Parse(args[2:])

	// 验 args JSON 合法
	var parsed any
	if err := json.Unmarshal([]byte(*argsJSON), &parsed); err != nil {
		return fmt.Errorf("--args 不是合法 JSON: %w", err)
	}

	// 后端 endpoint 是 POST /weaver/apps/<app>/invoke, body 含 invocation_id + args.
	// (之前误写成 /invocations/<id> path 风格, 直接 404.)
	url := pentaloomURL(fmt.Sprintf("/weaver/apps/%s/invoke", urlEscape(app)))
	wrapBody := fmt.Sprintf(`{"invocation_id":%q,"args":%s}`, invID, *argsJSON)
	req, err := http.NewRequest("POST", url, strings.NewReader(wrapBody))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("POST %s: %w (PentaLoom server 在跑吗?)", url, err)
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return fmt.Errorf("invoke %d: %s", resp.StatusCode, string(respBody))
	}
	fmt.Println(string(respBody))
	return nil
}

func urlEscape(s string) string {
	// 简单 escape, app/invocation_id 只允许 [a-z0-9_-] + 字母数字 (validator 强制),
	// 实际不会出现需 escape 的字符. 留个底以防变换.
	r := strings.NewReplacer(" ", "%20", "/", "%2F")
	return r.Replace(s)
}

// ─── service-port ───────────────────────────────────────────────────

func cmdServicePort(args []string) error {
	if len(args) < 2 {
		return fmt.Errorf("service-port requires <app> <service-name>")
	}
	app, svc := args[0], args[1]
	portFile := filepath.Join(runtimeDir(app), svc+".port")
	data, err := os.ReadFile(portFile)
	if err != nil {
		return fmt.Errorf("port file not found: %s (service 启动了吗?)", portFile)
	}
	portStr := strings.TrimSpace(string(data))
	// 验数字, 给清晰错
	if _, err := strconv.Atoi(portStr); err != nil {
		return fmt.Errorf("port file 内容不是数字: %q", portStr)
	}
	fmt.Println(portStr)
	return nil
}

// ─── files-dir ──────────────────────────────────────────────────────

func cmdFilesDir(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("files-dir requires <app>")
	}
	app := args[0]
	dir := filesDirOf(app)
	if _, err := os.Stat(dir); err != nil {
		return fmt.Errorf("files dir 不存在: %s", dir)
	}
	fmt.Println(dir)
	return nil
}

// ─── app.json window spec parse (open --entry 省略时用) ────────────

type windowSpec struct {
	Name   string `json:"name"`
	Entry  string `json:"entry"`
	Title  string `json:"title,omitempty"`
	Width  int    `json:"width,omitempty"`
	Height int    `json:"height,omitempty"`
}

type appJSONComponents struct {
	Windows []windowSpec `json:"windows,omitempty"`
}

type appJSON struct {
	Components appJSONComponents `json:"components"`
}

func readWindowSpec(app, window string) (*windowSpec, error) {
	path := filepath.Join(appDir(app), "app.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var aj appJSON
	if err := json.Unmarshal(data, &aj); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	for _, w := range aj.Components.Windows {
		if w.Name == window {
			return &w, nil
		}
	}
	return nil, fmt.Errorf("window %q 在 %s 里没声明", window, path)
}

func truncStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n-1] + "…"
}
