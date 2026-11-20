// Package server exposes the hub as an SSE endpoint plus a health check.
//
// The stream is served at the same path as Wikimedia EventStreams
// (/v2/stream/recentchange), so the Python backend only needs its stream URL
// pointed at this service.
package server

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"wikipulse/ingest/internal/hub"
)

// Server serves the SSE stream and health endpoint.
type Server struct {
	hub       *hub.Hub
	heartbeat time.Duration
	health    func() any
	logger    *slog.Logger
}

// New creates a Server. health returns the JSON body for GET /healthz.
func New(h *hub.Hub, heartbeat time.Duration, health func() any, logger *slog.Logger) *Server {
	if heartbeat <= 0 {
		heartbeat = 15 * time.Second
	}
	if logger == nil {
		logger = slog.Default()
	}
	return &Server{hub: h, heartbeat: heartbeat, health: health, logger: logger}
}

// Handler returns the HTTP routes.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v2/stream/recentchange", s.stream)
	mux.HandleFunc("GET /stream", s.stream)
	mux.HandleFunc("GET /healthz", s.healthz)
	return mux
}

const writeTimeout = 10 * time.Second

func (s *Server) stream(w http.ResponseWriter, r *http.Request) {
	rc := http.NewResponseController(w)

	h := w.Header()
	h.Set("Content-Type", "text/event-stream; charset=utf-8")
	h.Set("Cache-Control", "no-cache")
	h.Set("Connection", "keep-alive")
	h.Set("X-Accel-Buffering", "no") // disable proxy buffering (nginx)
	w.WriteHeader(http.StatusOK)

	sub, backlog := s.hub.Subscribe(r.Header.Get("Last-Event-ID"))
	defer s.hub.Unsubscribe(sub)

	// A dead client must not hold this goroutine forever: every write has a deadline.
	write := func(chunk string) bool {
		_ = rc.SetWriteDeadline(time.Now().Add(writeTimeout))
		if _, err := w.Write([]byte(chunk)); err != nil {
			return false
		}
		return rc.Flush() == nil
	}

	if !write(":ok\n\n") {
		return
	}
	for _, m := range backlog {
		if !write(s.frame(m)) {
			return
		}
	}

	ticker := time.NewTicker(s.heartbeat)
	defer ticker.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case m, ok := <-sub.Messages():
			if !ok {
				s.logger.Info("disconnecting slow subscriber", "remote", r.RemoteAddr)
				return
			}
			if !write(s.frame(m)) {
				return
			}
		case <-ticker.C:
			if !write(":heartbeat\n\n") {
				return
			}
		}
	}
}

func (s *Server) frame(m hub.Message) string {
	return "id: " + s.hub.EventID(m.Seq) + "\ndata: " + string(m.Data) + "\n\n"
}

func (s *Server) healthz(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(s.health()); err != nil {
		s.logger.Warn("could not write health response", "error", err)
	}
}
