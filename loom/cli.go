// cli.go — install/uninstall/open/close/status 子命令实现, 跟 main.go (start daemon)
// 拆开是因为 start 在 daemon 角色, 这些都是 client 角色, 走 IPC 调远程 daemon.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"text/template"

	"github.com/guyi-a/PentaLoom/loom/internal/protocol"
	"github.com/guyi-a/PentaLoom/loom/internal/socket"
)

const plistLabel = "com.pentaloom.loom"

func defaultSocketPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".pentaloom", "loom.sock")
}

func defaultLoomerPath() string {
	// LOOM_LOOMER_PATH 给开发态用 (Makefile loom-dev export, 指 ./.bin/loomer);
	// 装系统后走 ~/.pentaloom/bin/loomer.
	if p := os.Getenv("LOOM_LOOMER_PATH"); p != "" {
		return p
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".pentaloom", "bin", "loomer")
}

func defaultLoomBinaryPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".pentaloom", "bin", "loom")
}

func defaultLogDir() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".pentaloom", "logs")
}

func defaultPlistPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, "Library", "LaunchAgents", plistLabel+".plist")
}

// ─── install / uninstall ────────────────────────────────────────────

type plistVars struct {
	LoomBinary string
	LogDir     string
	Home       string
}

func cmdInstall() error {
	loomBin := defaultLoomBinaryPath()
	if _, err := os.Stat(loomBin); err != nil {
		return fmt.Errorf("loom binary not found at %s — copy it there first or run `make loom-install`", loomBin)
	}

	// log 目录
	if err := os.MkdirAll(defaultLogDir(), 0o755); err != nil {
		return fmt.Errorf("mkdir logs: %w", err)
	}

	// 渲 plist 模板
	home, _ := os.UserHomeDir()
	tmpl, err := template.New("plist").Parse(plistTmpl)
	if err != nil {
		return fmt.Errorf("parse plist tmpl: %w", err)
	}
	plistPath := defaultPlistPath()
	if err := os.MkdirAll(filepath.Dir(plistPath), 0o755); err != nil {
		return fmt.Errorf("mkdir LaunchAgents: %w", err)
	}
	f, err := os.Create(plistPath)
	if err != nil {
		return fmt.Errorf("create plist: %w", err)
	}
	defer f.Close()
	if err := tmpl.Execute(f, plistVars{
		LoomBinary: loomBin,
		LogDir:     defaultLogDir(),
		Home:       home,
	}); err != nil {
		return fmt.Errorf("write plist: %w", err)
	}

	// launchctl load — 已 load 的话先 unload (idempotent).
	_ = exec.Command("launchctl", "unload", plistPath).Run()
	if out, err := exec.Command("launchctl", "load", plistPath).CombinedOutput(); err != nil {
		return fmt.Errorf("launchctl load: %w (output: %s)", err, strings.TrimSpace(string(out)))
	}

	fmt.Printf("loom: installed → %s\n", plistPath)
	fmt.Printf("loom: daemon launched, log %s/loom.{stdout,stderr}.log\n", defaultLogDir())
	return nil
}

func cmdUninstall() error {
	plistPath := defaultPlistPath()
	// 即便文件不存在也 try unload (无害).
	_ = exec.Command("launchctl", "unload", plistPath).Run()
	if err := os.Remove(plistPath); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			fmt.Printf("loom: not installed (%s 不存在)\n", plistPath)
			return nil
		}
		return fmt.Errorf("rm plist: %w", err)
	}
	fmt.Printf("loom: uninstalled (removed %s)\n", plistPath)
	return nil
}

// ─── open / close / status (IPC client) ─────────────────────────────

func cmdOpen(args []string) error {
	fs := flag.NewFlagSet("open", flag.ExitOnError)
	entry := fs.String("entry", "", "path to .tsx entry (required)")
	title := fs.String("title", "", "window title")
	width := fs.Int("width", 0, "window width")
	height := fs.Int("height", 0, "window height")
	app := fs.String("app", "", "weaver app name (用于 close-by-name 二级索引)")
	winName := fs.String("window-name", "", "app.json components.windows[].name")
	_ = fs.Parse(args)
	if *entry == "" {
		return fmt.Errorf("loom open: --entry required")
	}
	abs, err := filepath.Abs(*entry)
	if err != nil {
		return fmt.Errorf("resolve entry: %w", err)
	}
	if _, err := os.Stat(abs); err != nil {
		return fmt.Errorf("entry not found: %s", abs)
	}

	payload, _ := json.Marshal(protocol.WindowOpenReq{
		EntryPath:  abs,
		Title:      *title,
		Width:      *width,
		Height:     *height,
		App:        *app,
		WindowName: *winName,
	})
	resp, err := socket.Send(defaultSocketPath(), &protocol.Request{
		ID:   newReqID(),
		Cmd:  protocol.CmdWindowOpen,
		Data: payload,
	})
	if err != nil {
		return err
	}
	if !resp.Ok {
		return fmt.Errorf("open failed: %s", resp.Error)
	}
	var r protocol.WindowOpenResp
	_ = json.Unmarshal(resp.Data, &r)
	fmt.Printf("loom: opened window=%s pid=%d\n", r.WindowID, r.PID)
	return nil
}

func cmdClose(args []string) error {
	if len(args) < 1 {
		return fmt.Errorf("loom close: usage `loom close <window-id>`")
	}
	id := args[0]
	payload, _ := json.Marshal(protocol.WindowCloseReq{WindowID: id})
	resp, err := socket.Send(defaultSocketPath(), &protocol.Request{
		ID:   newReqID(),
		Cmd:  protocol.CmdWindowClose,
		Data: payload,
	})
	if err != nil {
		return err
	}
	if !resp.Ok {
		return fmt.Errorf("close failed: %s", resp.Error)
	}
	fmt.Printf("loom: closed window=%s\n", id)
	return nil
}

func cmdStatus() error {
	resp, err := socket.Send(defaultSocketPath(), &protocol.Request{
		ID:  newReqID(),
		Cmd: protocol.CmdWindowList,
	})
	if err != nil {
		return err
	}
	if !resp.Ok {
		return fmt.Errorf("status failed: %s", resp.Error)
	}
	var r protocol.WindowListResp
	_ = json.Unmarshal(resp.Data, &r)
	if len(r.Windows) == 0 {
		fmt.Println("loom: 0 windows")
		return nil
	}
	fmt.Printf("loom: %d window(s)\n", len(r.Windows))
	fmt.Printf("  %-12s %-6s %-30s %s\n", "ID", "PID", "TITLE", "ENTRY")
	for _, w := range r.Windows {
		title := w.Title
		if title == "" {
			title = "(untitled)"
		}
		fmt.Printf("  %-12s %-6d %-30s %s\n", w.WindowID, w.PID, truncStr(title, 28), w.EntryPath)
	}
	return nil
}

func newReqID() string {
	var b [4]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

func truncStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n-1] + "…"
}
