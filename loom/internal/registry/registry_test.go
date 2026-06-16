package registry

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/guyi-a/PentaLoom/loom/internal/protocol"
)

// TestOpenListClose: 用一个 stub bash script 当 fake loomer (阻塞 100s 接受任意 args),
// 验 spawn / list / kill 三件套 + reaper goroutine 清表.
func TestOpenListClose(t *testing.T) {
	// 写一个 stub: 任何 args 都接, 阻塞.
	stubPath := filepath.Join(t.TempDir(), "fake-loomer.sh")
	if err := os.WriteFile(stubPath, []byte("#!/bin/sh\nsleep 100\n"), 0o755); err != nil {
		t.Fatalf("write stub: %v", err)
	}

	r := New(stubPath)

	// Open: spawn cat (会一直等 stdin 阻塞).
	wp1, err := r.Open(&protocol.WindowOpenReq{EntryPath: "/tmp/fake1"})
	if err != nil {
		t.Fatalf("Open 1: %v", err)
	}
	if wp1.PID == 0 {
		t.Fatal("PID should be non-zero")
	}
	if wp1.ID == "" || len(wp1.ID) != 12 {
		t.Errorf("ID should be 12 hex, got %q", wp1.ID)
	}

	wp2, err := r.Open(&protocol.WindowOpenReq{EntryPath: "/tmp/fake2", Title: "second"})
	if err != nil {
		t.Fatalf("Open 2: %v", err)
	}

	// List: 应该有 2 个.
	list := r.List("")
	if len(list) != 2 {
		t.Errorf("expected 2 windows, got %d", len(list))
	}

	// Close 1.
	if err := r.Close(wp1.ID); err != nil {
		t.Fatalf("Close: %v", err)
	}

	// 给 reaper goroutine 一点时间清表.
	for i := 0; i < 20; i++ {
		if len(r.List("")) == 1 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if got := len(r.List("")); got != 1 {
		t.Errorf("expected 1 window after close, got %d", got)
	}

	// 再 Close 同一个 id 应该 not found.
	if err := r.Close(wp1.ID); err == nil {
		t.Error("Close on dead id should error")
	}

	// KillAll cleanup.
	r.KillAll()
	for i := 0; i < 20; i++ {
		if len(r.List("")) == 0 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if got := len(r.List("")); got != 0 {
		t.Errorf("expected 0 windows after KillAll, got %d", got)
	}
	_ = wp2
}
