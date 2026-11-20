package server

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"wikipulse/ingest/internal/hub"
	"wikipulse/ingest/internal/sse"
)

func newTestServer(t *testing.T, h *hub.Hub, heartbeat time.Duration) *httptest.Server {
	t.Helper()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	s := New(h, heartbeat, func() any { return map[string]any{"status": "ok", "hub": h.Stats()} }, logger)
	srv := httptest.NewServer(s.Handler())
	t.Cleanup(srv.Close)
	return srv
}

func openStream(t *testing.T, ctx context.Context, url, lastEventID string) *http.Response {
	t.Helper()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		t.Fatal(err)
	}
	if lastEventID != "" {
		req.Header.Set("Last-Event-ID", lastEventID)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func waitForSubscribers(t *testing.T, h *hub.Hub, n int) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for h.Stats().Subscribers != n {
		if time.Now().After(deadline) {
			t.Fatalf("subscribers = %d, want %d", h.Stats().Subscribers, n)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestStreamDeliversLiveEventsWithIDs(t *testing.T) {
	h := hub.New("boot", 16, 16)
	srv := newTestServer(t, h, time.Minute)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp := openStream(t, ctx, srv.URL+"/v2/stream/recentchange", "")
	if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "text/event-stream") {
		t.Fatalf("content type = %q", ct)
	}
	waitForSubscribers(t, h, 1)
	h.Publish([]byte(`{"title":"A"}`))
	h.Publish([]byte(`{"title":"B"}`))

	r := sse.NewReader(resp.Body)
	for _, want := range []string{"A", "B"} {
		ev, err := r.Next()
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(ev.Data, want) {
			t.Errorf("event data = %q, want title %s", ev.Data, want)
		}
	}
	if r.LastID() != h.EventID(2) {
		t.Errorf("last id = %q, want %q", r.LastID(), h.EventID(2))
	}
}

func TestStreamReplaysBacklogOnReconnect(t *testing.T) {
	h := hub.New("boot", 16, 16)
	srv := newTestServer(t, h, time.Minute)
	for _, title := range []string{"1", "2", "3"} {
		h.Publish([]byte(title))
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp := openStream(t, ctx, srv.URL+"/stream", h.EventID(1))
	r := sse.NewReader(resp.Body)
	for _, want := range []string{"2", "3"} {
		ev, err := r.Next()
		if err != nil {
			t.Fatal(err)
		}
		if ev.Data != want {
			t.Errorf("replayed %q, want %q", ev.Data, want)
		}
	}
}

func TestHeartbeatsKeepIdleStreamsAlive(t *testing.T) {
	h := hub.New("boot", 4, 4)
	srv := newTestServer(t, h, 50*time.Millisecond)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	resp := openStream(t, ctx, srv.URL+"/stream", "")
	buf := make([]byte, 0, 256)
	chunk := make([]byte, 64)
	for !strings.Contains(string(buf), ":heartbeat") {
		n, err := resp.Body.Read(chunk)
		if err != nil {
			t.Fatalf("stream ended before a heartbeat: %v (got %q)", err, buf)
		}
		buf = append(buf, chunk[:n]...)
	}
}

func TestClientDisconnectUnsubscribes(t *testing.T) {
	h := hub.New("boot", 4, 4)
	srv := newTestServer(t, h, time.Minute)
	ctx, cancel := context.WithCancel(context.Background())

	openStream(t, ctx, srv.URL+"/stream", "")
	waitForSubscribers(t, h, 1)
	cancel()
	waitForSubscribers(t, h, 0)
}

func TestHealthzReturnsJSON(t *testing.T) {
	h := hub.New("boot", 4, 4)
	srv := newTestServer(t, h, time.Minute)

	resp, err := http.Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK || body["status"] != "ok" {
		t.Fatalf("status %d body %v", resp.StatusCode, body)
	}
}

func TestUnknownRoutesAndMethodsAreRejected(t *testing.T) {
	h := hub.New("boot", 4, 4)
	srv := newTestServer(t, h, time.Minute)

	resp, err := http.Get(srv.URL + "/nope")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("unknown route status = %d", resp.StatusCode)
	}

	resp, err = http.Post(srv.URL+"/stream", "text/plain", nil)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("POST status = %d", resp.StatusCode)
	}
}
