// Package pipeline turns raw upstream SSE events into validated, de-duplicated
// edits and publishes them to the hub.
package pipeline

import (
	"log/slog"
	"sync"
	"time"

	"wikipulse/ingest/internal/edits"
	"wikipulse/ingest/internal/hub"
	"wikipulse/ingest/internal/sse"
)

// Counters is a snapshot of pipeline throughput.
type Counters struct {
	Received   uint64 `json:"received"`
	Forwarded  uint64 `json:"forwarded"`
	Filtered   uint64 `json:"filtered"`
	Malformed  uint64 `json:"malformed"`
	Duplicates uint64 `json:"duplicates"`
	// LastEventAgeSeconds is -1 until the first edit has been forwarded.
	LastEventAgeSeconds float64 `json:"last_event_age_seconds"`
}

// Pipeline is safe for concurrent use.
type Pipeline struct {
	hub    *hub.Hub
	logger *slog.Logger
	now    func() time.Time

	mu        sync.Mutex
	dedupe    *edits.Deduper
	counters  Counters
	lastEvent time.Time
}

// New creates a pipeline publishing to h, remembering dedupeSize recent edit keys.
func New(h *hub.Hub, dedupeSize int, logger *slog.Logger) *Pipeline {
	if logger == nil {
		logger = slog.Default()
	}
	return &Pipeline{hub: h, logger: logger, now: time.Now, dedupe: edits.NewDeduper(dedupeSize)}
}

// Handle processes one upstream event.
func (p *Pipeline) Handle(ev sse.Event) {
	edit, result := edits.Normalize([]byte(ev.Data))

	p.mu.Lock()
	p.counters.Received++
	switch result {
	case edits.Filtered:
		p.counters.Filtered++
		p.mu.Unlock()
		return
	case edits.Malformed:
		p.counters.Malformed++
		p.mu.Unlock()
		return
	}
	if p.dedupe.Seen(edit.Key()) {
		p.counters.Duplicates++
		p.mu.Unlock()
		return
	}
	p.mu.Unlock()

	payload, err := edit.Encode()
	if err != nil {
		p.mu.Lock()
		p.counters.Malformed++
		p.mu.Unlock()
		p.logger.Warn("could not encode edit", "id", edit.Key(), "error", err)
		return
	}
	p.hub.Publish(payload)

	p.mu.Lock()
	p.counters.Forwarded++
	p.lastEvent = p.now()
	p.mu.Unlock()
}

// Counters returns a snapshot of the pipeline counters.
func (p *Pipeline) Counters() Counters {
	p.mu.Lock()
	defer p.mu.Unlock()
	c := p.counters
	c.LastEventAgeSeconds = -1
	if !p.lastEvent.IsZero() {
		c.LastEventAgeSeconds = p.now().Sub(p.lastEvent).Round(time.Millisecond).Seconds()
	}
	return c
}
