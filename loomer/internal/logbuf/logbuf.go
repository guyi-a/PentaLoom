// Package logbuf — per-window 的 console.log/warn/error ring buffer.
// loomer JS 端 hook console 后把 entry 发到 Go 这边累积, agent 拉 /logs
// endpoint 拿最近 N 条诊断 window 内 React 报错 / fetch 失败.
//
// ring 简单实现: slice + cursor + mutex. 上限 default 500 条 (per-window
// 内存 ~500 lines × 平均 500 字节 ≈ 250KB, 可接受).
package logbuf

import (
	"sync"
	"time"
)

// Entry 一条 log 记录. Level 是 "log" / "warn" / "error" / "info" / "debug",
// 跟 JS console method 名对齐. Args 是 JS 端 stringify 后的字符串数组
// (Go 端不重新解析, JS 端 try JSON 不行就 String() fallback).
type Entry struct {
	Time  time.Time `json:"time"`
	Level string    `json:"level"`
	Args  []string  `json:"args"`
}

// Ring 固定容量 ring buffer. 满了覆盖最早. 线程安全.
type Ring struct {
	mu       sync.Mutex
	buf      []Entry
	capacity int
	// next 写入 buf 的下一位置 (0..capacity-1). 满了之后绕回 0.
	next int
	// 累计写入条数; len(buf) <= capacity, 用 written 算 wraparound.
	written int
}

// New 创建 ring, capacity 必须 >= 1.
func New(capacity int) *Ring {
	if capacity < 1 {
		capacity = 1
	}
	return &Ring{
		buf:      make([]Entry, capacity),
		capacity: capacity,
	}
}

// Append 添加一条. 满了覆盖最早.
func (r *Ring) Append(level string, args []string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.buf[r.next] = Entry{
		Time:  time.Now(),
		Level: level,
		Args:  args,
	}
	r.next = (r.next + 1) % r.capacity
	r.written++
}

// Tail 返回最近 n 条 (按时间正序: 最早→最晚). n 超出实际持有数则只返实有.
// 拷贝返回 slice, 调用方修改不影响 ring.
func (r *Ring) Tail(n int) []Entry {
	r.mu.Lock()
	defer r.mu.Unlock()
	have := r.written
	if have > r.capacity {
		have = r.capacity
	}
	if n <= 0 || n > have {
		n = have
	}
	out := make([]Entry, n)
	// ring 起点: 写过 written 次, 最早一条在 (next - have) % capacity, 最晚一条
	// 在 (next - 1 + capacity) % capacity. 我们要末 n 条, 起点 (next - n) % capacity.
	start := (r.next - n + r.capacity*2) % r.capacity
	for i := 0; i < n; i++ {
		out[i] = r.buf[(start+i)%r.capacity]
	}
	return out
}

// Len 当前持有条数 (≤ capacity).
func (r *Ring) Len() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.written > r.capacity {
		return r.capacity
	}
	return r.written
}
