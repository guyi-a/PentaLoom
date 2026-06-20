package logbuf

import (
	"testing"
)

func TestRing_AppendAndTail(t *testing.T) {
	r := New(3)
	r.Append("log", []string{"hello"})
	r.Append("warn", []string{"world"})
	out := r.Tail(10)
	if len(out) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(out))
	}
	if out[0].Level != "log" || out[0].Args[0] != "hello" {
		t.Errorf("entry 0 wrong: %+v", out[0])
	}
	if out[1].Level != "warn" || out[1].Args[0] != "world" {
		t.Errorf("entry 1 wrong: %+v", out[1])
	}
}

func TestRing_Wraparound(t *testing.T) {
	r := New(3)
	for i := 0; i < 5; i++ {
		r.Append("log", []string{string(rune('a' + i))})
	}
	if r.Len() != 3 {
		t.Errorf("expected len 3 after 5 appends to cap-3 ring, got %d", r.Len())
	}
	out := r.Tail(3)
	expected := []string{"c", "d", "e"} // 最早 a, b 已被覆盖
	for i, want := range expected {
		if out[i].Args[0] != want {
			t.Errorf("entry %d: expected %q, got %q", i, want, out[i].Args[0])
		}
	}
}

func TestRing_TailFewer(t *testing.T) {
	r := New(10)
	for i := 0; i < 5; i++ {
		r.Append("log", []string{string(rune('a' + i))})
	}
	out := r.Tail(2)
	if len(out) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(out))
	}
	// 末 2 条是 'd', 'e'
	if out[0].Args[0] != "d" || out[1].Args[0] != "e" {
		t.Errorf("Tail(2) wrong: %+v", out)
	}
}

func TestRing_TailZeroReturnsAll(t *testing.T) {
	r := New(10)
	r.Append("log", []string{"x"})
	r.Append("log", []string{"y"})
	out := r.Tail(0)
	if len(out) != 2 {
		t.Errorf("Tail(0) should return all (2), got %d", len(out))
	}
}
