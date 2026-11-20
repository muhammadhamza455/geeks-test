// Package upstream maintains the long-lived connection to Wikimedia EventStreams.
package upstream

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math/rand/v2"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"wikipulse/ingest/internal/sse"
)

// Config controls the upstream connection.
type Config struct {
	URL         string
	UserAgent   string
	MinBackoff  time.Duration
	MaxBackoff  time.Duration
	IdleTimeout time.Duration // reconnect if the stream sends nothing for this long
	HTTPClient  *http.Client  // optional; a streaming-safe default is used when nil
}

// Client streams events from the upstream SSE endpoint, reconnecting forever.
type Client struct {
	cfg    Config
	http   *http.Client
	logger *slog.Logger

	connected  atomic.Bool
	reconnects atomic.Int64

	mu          sync.Mutex
	lastEventID string
}

// New creates a Client, filling in defaults for unset durations.
func New(cfg Config, logger *slog.Logger) *Client {
	if cfg.MinBackoff <= 0 {
		cfg.MinBackoff = time.Second
	}
	if cfg.MaxBackoff < cfg.MinBackoff {
		cfg.MaxBackoff = 30 * time.Second
	}
	if cfg.IdleTimeout <= 0 {
		cfg.IdleTimeout = 60 * time.Second
	}
	hc := cfg.HTTPClient
	if hc == nil {
		hc = &http.Client{
			// No overall Timeout: the response body is an endless stream.
			Transport: &http.Transport{
				Proxy:                 http.ProxyFromEnvironment,
				DialContext:           (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
				TLSHandshakeTimeout:   15 * time.Second,
				ResponseHeaderTimeout: 20 * time.Second,
				ForceAttemptHTTP2:     true,
			},
		}
	}
	if logger == nil {
		logger = slog.Default()
	}
	return &Client{cfg: cfg, http: hc, logger: logger}
}

// Connected reports whether a stream is currently open.
func (c *Client) Connected() bool { return c.connected.Load() }

// Reconnects returns how many times the connection has been re-established.
func (c *Client) Reconnects() int64 { return c.reconnects.Load() }

// LastEventID returns the last upstream event ID, used to resume after a reconnect.
func (c *Client) LastEventID() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.lastEventID
}

// Run streams until ctx is cancelled, calling onEvent for every event. It only
// returns ctx.Err(); connection failures are logged and retried with
// exponential backoff plus jitter.
func (c *Client) Run(ctx context.Context, onEvent func(sse.Event)) error {
	backoff := c.cfg.MinBackoff
	attempt := 0
	for {
		attempt++
		if attempt > 1 {
			c.reconnects.Add(1)
		}
		gotEvents, err := c.streamOnce(ctx, onEvent)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if gotEvents {
			backoff = c.cfg.MinBackoff
		}
		wait := backoff + time.Duration(rand.Int64N(int64(backoff)/2+1))
		c.logger.Warn("upstream stream ended; reconnecting", "error", errString(err), "retry_in", wait.Round(time.Millisecond).String())

		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
		backoff = min(backoff*2, c.cfg.MaxBackoff)
	}
}

// streamOnce opens one connection and reads it until it ends. It reports whether
// any event was received (used to reset the backoff).
func (c *Client) streamOnce(ctx context.Context, onEvent func(sse.Event)) (bool, error) {
	connCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	req, err := http.NewRequestWithContext(connCtx, http.MethodGet, c.cfg.URL, nil)
	if err != nil {
		return false, err
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Cache-Control", "no-cache")
	if c.cfg.UserAgent != "" {
		req.Header.Set("User-Agent", c.cfg.UserAgent)
	}
	if id := c.LastEventID(); id != "" {
		req.Header.Set("Last-Event-ID", id)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		return false, fmt.Errorf("upstream returned HTTP %d", resp.StatusCode)
	}

	c.connected.Store(true)
	defer c.connected.Store(false)
	c.logger.Info("connected to upstream", "url", c.cfg.URL)

	// Watchdog: if no bytes arrive for IdleTimeout, cancel the request so a
	// silently stalled connection is replaced instead of hanging forever.
	watchdog := time.AfterFunc(c.cfg.IdleTimeout, cancel)
	defer watchdog.Stop()
	body := &activityReader{r: resp.Body, onRead: func() { watchdog.Reset(c.cfg.IdleTimeout) }}

	reader := sse.NewReader(body)
	gotEvents := false
	for {
		ev, err := reader.Next()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return gotEvents, errors.New("upstream closed the stream")
			}
			if connCtx.Err() != nil && ctx.Err() == nil {
				return gotEvents, fmt.Errorf("no data for %s", c.cfg.IdleTimeout)
			}
			return gotEvents, err
		}
		gotEvents = true
		if ev.ID != "" {
			c.mu.Lock()
			c.lastEventID = ev.ID
			c.mu.Unlock()
		}
		onEvent(ev)
	}
}

type activityReader struct {
	r      io.Reader
	onRead func()
}

func (a *activityReader) Read(p []byte) (int, error) {
	n, err := a.r.Read(p)
	if n > 0 {
		a.onRead()
	}
	return n, err
}

func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
