package upstream

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"wikipulse/ingest/internal/sse"
)

func quietLogger() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func collect(t *testing.T, c *Client, want int, timeout time.Duration) []sse.Event {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	var mu sync.Mutex
	var got []sse.Event
	done := make(chan struct{})
	go func() {
		_ = c.Run(ctx, func(ev sse.Event) {
			mu.Lock()
			got = append(got, ev)
			n := len(got)
			mu.Unlock()
			if n == want {
				cancel()
			}
		})
		close(done)
	}()
	<-done

	mu.Lock()
	defer mu.Unlock()
	if len(got) < want {
		t.Fatalf("got %d events before timeout, want %d", len(got), want)
	}
	return got
}

func TestReconnectsAndResumesWithLastEventID(t *testing.T) {
	var conns atomic.Int32
	var resumeID atomic.Value
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Accept") != "text/event-stream" || r.Header.Get("User-Agent") != "test-agent" {
			http.Error(w, "bad headers", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		switch conns.Add(1) {
		case 1:
			fmt.Fprint(w, ":ok\n\nid: u1\ndata: one\n\nid: u2\ndata: two\n\n")
			// returning closes the stream, forcing a reconnect
		default:
			resumeID.Store(r.Header.Get("Last-Event-ID"))
			fmt.Fprint(w, "id: u3\ndata: three\n\n")
			w.(http.Flusher).Flush()
			<-r.Context().Done()
		}
	}))
	defer srv.Close()

	c := New(Config{URL: srv.URL, UserAgent: "test-agent", MinBackoff: 10 * time.Millisecond, MaxBackoff: 20 * time.Millisecond}, quietLogger())
	got := collect(t, c, 3, 5*time.Second)

	if got[0].Data != "one" || got[2].Data != "three" {
		t.Fatalf("unexpected events: %+v", got)
	}
	if id, _ := resumeID.Load().(string); id != "u2" {
		t.Errorf("reconnect should send Last-Event-ID u2, got %q", id)
	}
	if c.Reconnects() < 1 {
		t.Errorf("expected at least one reconnect, got %d", c.Reconnects())
	}
	if c.LastEventID() != "u3" {
		t.Errorf("last event id = %q", c.LastEventID())
	}
}

func TestRetriesAfterHTTPErrors(t *testing.T) {
	var conns atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if conns.Add(1) <= 2 {
			http.Error(w, "busy", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, "data: ok\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer srv.Close()

	c := New(Config{URL: srv.URL, MinBackoff: 5 * time.Millisecond, MaxBackoff: 10 * time.Millisecond}, quietLogger())
	got := collect(t, c, 1, 5*time.Second)
	if got[0].Data != "ok" || conns.Load() < 3 {
		t.Fatalf("expected success on third attempt, conns=%d events=%+v", conns.Load(), got)
	}
}

func TestIdleWatchdogReplacesSilentConnection(t *testing.T) {
	var conns atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		if conns.Add(1) == 1 {
			fmt.Fprint(w, ":ok\n\n") // then go silent without closing
			w.(http.Flusher).Flush()
			<-r.Context().Done()
			return
		}
		fmt.Fprint(w, "data: after-stall\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer srv.Close()

	c := New(Config{URL: srv.URL, MinBackoff: 5 * time.Millisecond, MaxBackoff: 10 * time.Millisecond, IdleTimeout: 150 * time.Millisecond}, quietLogger())
	got := collect(t, c, 1, 5*time.Second)
	if got[0].Data != "after-stall" || conns.Load() < 2 {
		t.Fatalf("stalled connection was not replaced: conns=%d events=%+v", conns.Load(), got)
	}
}

func TestRunStopsPromptlyOnCancel(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	defer srv.Close()

	c := New(Config{URL: srv.URL}, quietLogger())
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- c.Run(ctx, func(sse.Event) {}) }()

	deadline := time.Now().Add(2 * time.Second)
	for !c.Connected() && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if !c.Connected() {
		t.Fatal("never connected")
	}
	cancel()
	select {
	case err := <-done:
		if err != context.Canceled {
			t.Fatalf("Run returned %v, want context.Canceled", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not stop after cancel")
	}
	if c.Connected() {
		t.Error("Connected should be false after stop")
	}
}
