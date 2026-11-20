// Command ingest is WikiPulse's Go ingestion service.
//
// It holds the single long-lived connection to Wikimedia EventStreams, filters
// the firehose down to page edits, validates and de-duplicates them, and re-serves
// a slimmed SSE stream that any number of WikiPulse backend instances can consume.
//
// Configuration (environment variables):
//
//	INGEST_LISTEN_ADDR          listen address (default ":8090")
//	INGEST_UPSTREAM_URL         upstream SSE URL (default Wikimedia recentchange)
//	INGEST_USER_AGENT           User-Agent sent upstream (Wikimedia asks for contact info)
//	INGEST_REPLAY_BUFFER        events kept for Last-Event-ID resume (default 4096)
//	INGEST_SUBSCRIBER_BUFFER    per-subscriber queue before it is dropped as slow (default 2048)
//	INGEST_DEDUPE_SIZE          recent edit IDs remembered for de-duplication (default 20000)
//	INGEST_HEARTBEAT_SECONDS    downstream keep-alive comment interval (default 15)
//	INGEST_IDLE_TIMEOUT_SECONDS reconnect upstream after this long without data (default 60)
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"wikipulse/ingest/internal/hub"
	"wikipulse/ingest/internal/pipeline"
	"wikipulse/ingest/internal/server"
	"wikipulse/ingest/internal/upstream"
)

type config struct {
	ListenAddr       string
	UpstreamURL      string
	UserAgent        string
	ReplayBuffer     int
	SubscriberBuffer int
	DedupeSize       int
	Heartbeat        time.Duration
	IdleTimeout      time.Duration
}

func loadConfig(getenv func(string) string) config {
	str := func(key, def string) string {
		if v := strings.TrimSpace(getenv(key)); v != "" {
			return v
		}
		return def
	}
	num := func(key string, def, minimum int) int {
		v, err := strconv.Atoi(strings.TrimSpace(getenv(key)))
		if err != nil || v < minimum {
			return def
		}
		return v
	}
	return config{
		ListenAddr:       str("INGEST_LISTEN_ADDR", ":8090"),
		UpstreamURL:      str("INGEST_UPSTREAM_URL", "https://stream.wikimedia.org/v2/stream/recentchange"),
		UserAgent:        str("INGEST_USER_AGENT", "WikiPulse-Ingest/1.0 (https://github.com/wikipulse/wikipulse; dev@wikipulse.local)"),
		ReplayBuffer:     num("INGEST_REPLAY_BUFFER", 4096, 1),
		SubscriberBuffer: num("INGEST_SUBSCRIBER_BUFFER", 2048, 1),
		DedupeSize:       num("INGEST_DEDUPE_SIZE", 20000, 1),
		Heartbeat:        time.Duration(num("INGEST_HEARTBEAT_SECONDS", 15, 1)) * time.Second,
		IdleTimeout:      time.Duration(num("INGEST_IDLE_TIMEOUT_SECONDS", 60, 5)) * time.Second,
	}
}

func newBootID() string {
	b := make([]byte, 6)
	if _, err := rand.Read(b); err != nil {
		return strconv.FormatInt(time.Now().UnixNano(), 36)
	}
	return hex.EncodeToString(b)
}

func main() {
	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	cfg := loadConfig(os.Getenv)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	h := hub.New(newBootID(), cfg.ReplayBuffer, cfg.SubscriberBuffer)
	pipe := pipeline.New(h, cfg.DedupeSize, logger)
	up := upstream.New(upstream.Config{
		URL:         cfg.UpstreamURL,
		UserAgent:   cfg.UserAgent,
		IdleTimeout: cfg.IdleTimeout,
	}, logger)

	started := time.Now()
	health := func() any {
		status := "ok"
		if !up.Connected() {
			status = "degraded"
		}
		return map[string]any{
			"status":              status,
			"upstream_url":        cfg.UpstreamURL,
			"upstream_connected":  up.Connected(),
			"upstream_reconnects": up.Reconnects(),
			"pipeline":            pipe.Counters(),
			"hub":                 h.Stats(),
			"uptime_seconds":      time.Since(started).Round(time.Second).Seconds(),
		}
	}

	srv := &http.Server{
		Addr:              cfg.ListenAddr,
		Handler:           server.New(h, cfg.Heartbeat, health, logger).Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		// No WriteTimeout: SSE responses are long-lived; per-write deadlines are set in the handler.
		// Request contexts derive from ctx so open streams end promptly on shutdown.
		BaseContext: func(net.Listener) context.Context { return ctx },
	}

	go func() {
		_ = up.Run(ctx, pipe.Handle)
	}()

	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}()

	logger.Info("ingest service listening", "addr", cfg.ListenAddr, "upstream", cfg.UpstreamURL)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Error("server failed", "error", err)
		os.Exit(1)
	}
	logger.Info("ingest service stopped")
}
