package control

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/guyi-a/PentaLoom/loomer/internal/logbuf"
)

func TestServer_LogsEndpoint(t *testing.T) {
	ring := logbuf.New(10)
	ring.Append("log", []string{"hello"})
	ring.Append("error", []string{"oops", "{ }"})

	srv, err := New(ring, nil)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	go func() { _ = srv.Serve() }()
	defer srv.Close()

	port := srv.Port()
	if port <= 0 {
		t.Fatalf("invalid port %d", port)
	}

	url := "http://127.0.0.1:" + itoa(port) + "/logs?lines=10"
	resp, err := httpGetWithRetry(url, 3, 100*time.Millisecond)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("status: %d", resp.StatusCode)
	}
	body, _ := io.ReadAll(resp.Body)
	var out struct {
		Entries []struct {
			Level string   `json:"level"`
			Args  []string `json:"args"`
		} `json:"entries"`
	}
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatalf("parse JSON: %v\n%s", err, body)
	}
	if len(out.Entries) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(out.Entries))
	}
	if out.Entries[0].Level != "log" || out.Entries[0].Args[0] != "hello" {
		t.Errorf("entry 0 wrong: %+v", out.Entries[0])
	}
	if out.Entries[1].Level != "error" {
		t.Errorf("entry 1 wrong: %+v", out.Entries[1])
	}
}

func TestServer_ScreenshotEndpoint_NotImplemented(t *testing.T) {
	srv, err := New(logbuf.New(1), nil)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	go func() { _ = srv.Serve() }()
	defer srv.Close()

	url := "http://127.0.0.1:" + itoa(srv.Port()) + "/screenshot"
	resp, err := httpGetWithRetry(url, 3, 100*time.Millisecond)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotImplemented {
		t.Errorf("expected 501 got %d", resp.StatusCode)
	}
}

func TestServer_ScreenshotEndpoint_ReturnsPNGBytes(t *testing.T) {
	fakePNG := []byte{0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 'F', 'A', 'K', 'E'}
	called := 0
	fn := func() ([]byte, error) {
		called++
		return fakePNG, nil
	}
	srv, err := New(logbuf.New(1), fn)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	go func() { _ = srv.Serve() }()
	defer srv.Close()

	url := "http://127.0.0.1:" + itoa(srv.Port()) + "/screenshot"
	resp, err := httpGetWithRetry(url, 3, 100*time.Millisecond)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Errorf("status: %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "image/png" {
		t.Errorf("Content-Type: %q", ct)
	}
	body, _ := io.ReadAll(resp.Body)
	if string(body) != string(fakePNG) {
		t.Errorf("body bytes mismatch")
	}
	if called != 1 {
		t.Errorf("expected screenshot fn called once, got %d", called)
	}
}

func TestServer_HealthzEndpoint(t *testing.T) {
	srv, err := New(logbuf.New(1), nil)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	go func() { _ = srv.Serve() }()
	defer srv.Close()

	url := "http://127.0.0.1:" + itoa(srv.Port()) + "/healthz"
	resp, err := httpGetWithRetry(url, 3, 100*time.Millisecond)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "ok") {
		t.Errorf("expected body 'ok', got %q", body)
	}
}

func itoa(n int) string {
	// 不引 strconv (test 简单些)
	if n == 0 {
		return "0"
	}
	var buf [16]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	return string(buf[i:])
}

// httpGetWithRetry: server.Serve goroutine 还没起 listen 就 GET 会拒, 简单重试.
func httpGetWithRetry(url string, attempts int, delay time.Duration) (*http.Response, error) {
	var lastErr error
	for i := 0; i < attempts; i++ {
		resp, err := http.Get(url)
		if err == nil {
			return resp, nil
		}
		lastErr = err
		time.Sleep(delay)
	}
	return nil, lastErr
}
