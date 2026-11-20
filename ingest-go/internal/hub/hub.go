// Package hub fans one stream of messages out to many subscribers.
//
// It keeps a ring buffer of recent messages so a subscriber that reconnects
// with Last-Event-ID resumes without gaps, and it never lets a slow subscriber
// block the publisher: a subscriber whose buffer is full is disconnected (its
// channel is closed) and can reconnect to resume from the replay buffer.
package hub

import (
	"strconv"
	"strings"
	"sync"
)

// Message is one published payload with its sequence number.
type Message struct {
	Seq  uint64
	Data []byte
}

// Subscriber receives messages published after it subscribed.
type Subscriber struct {
	ch     chan Message
	closed bool // guarded by Hub.mu
}

// Messages returns the channel of live messages. It is closed when the
// subscriber is unsubscribed or disconnected for falling behind.
func (s *Subscriber) Messages() <-chan Message { return s.ch }

// Stats is a point-in-time snapshot of hub counters.
type Stats struct {
	Subscribers      int    `json:"subscribers"`
	Published        uint64 `json:"published"`
	SlowDisconnects  uint64 `json:"slow_disconnects"`
	ReplayedMessages uint64 `json:"replayed_messages"`
}

// Hub is safe for concurrent use.
type Hub struct {
	mu        sync.Mutex
	bootID    string
	seq       uint64
	ring      []Message
	start     int
	size      int
	subs      map[*Subscriber]struct{}
	subBuffer int
	slow      uint64
	replayed  uint64
}

// New creates a hub. bootID distinguishes this process's event IDs from a
// previous run's, replay is the ring-buffer size, and subBuffer is each
// subscriber's channel capacity.
func New(bootID string, replay, subBuffer int) *Hub {
	if replay < 1 {
		replay = 1
	}
	if subBuffer < 1 {
		subBuffer = 1
	}
	return &Hub{
		bootID:    bootID,
		ring:      make([]Message, replay),
		subs:      make(map[*Subscriber]struct{}),
		subBuffer: subBuffer,
	}
}

// EventID formats the SSE event ID for a sequence number.
func (h *Hub) EventID(seq uint64) string {
	return h.bootID + "-" + strconv.FormatUint(seq, 10)
}

// Publish stores data in the replay buffer and delivers it to every subscriber.
func (h *Hub) Publish(data []byte) Message {
	h.mu.Lock()
	defer h.mu.Unlock()

	h.seq++
	msg := Message{Seq: h.seq, Data: data}

	if h.size < len(h.ring) {
		h.ring[(h.start+h.size)%len(h.ring)] = msg
		h.size++
	} else {
		h.ring[h.start] = msg
		h.start = (h.start + 1) % len(h.ring)
	}

	for s := range h.subs {
		select {
		case s.ch <- msg:
		default:
			// Too slow: disconnect rather than block ingestion. The client
			// reconnects with Last-Event-ID and resumes from the ring buffer.
			h.removeLocked(s)
			h.slow++
		}
	}
	return msg
}

// Subscribe registers a subscriber and returns the backlog it missed, based on
// the client's Last-Event-ID (empty for a fresh connection). Backlog and live
// delivery are registered under one lock, so nothing is missed or duplicated.
func (h *Hub) Subscribe(lastEventID string) (*Subscriber, []Message) {
	h.mu.Lock()
	defer h.mu.Unlock()

	sub := &Subscriber{ch: make(chan Message, h.subBuffer)}
	h.subs[sub] = struct{}{}

	var backlog []Message
	if after, ok := h.parseOwnID(lastEventID); ok && after < h.seq {
		for i := 0; i < h.size; i++ {
			m := h.ring[(h.start+i)%len(h.ring)]
			if m.Seq > after {
				backlog = append(backlog, m)
			}
		}
		h.replayed += uint64(len(backlog))
	}
	return sub, backlog
}

// Unsubscribe removes the subscriber and closes its channel. It is idempotent.
func (h *Hub) Unsubscribe(s *Subscriber) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.removeLocked(s)
}

// Stats returns a snapshot of hub counters.
func (h *Hub) Stats() Stats {
	h.mu.Lock()
	defer h.mu.Unlock()
	return Stats{
		Subscribers:      len(h.subs),
		Published:        h.seq,
		SlowDisconnects:  h.slow,
		ReplayedMessages: h.replayed,
	}
}

func (h *Hub) removeLocked(s *Subscriber) {
	if s.closed {
		return
	}
	s.closed = true
	delete(h.subs, s)
	close(s.ch)
}

// parseOwnID returns the sequence number of an event ID issued by this process.
// IDs from another process (a restart) or from Wikimedia itself are ignored.
func (h *Hub) parseOwnID(id string) (uint64, bool) {
	boot, seqStr, found := strings.Cut(id, "-")
	if !found || boot != h.bootID {
		return 0, false
	}
	seq, err := strconv.ParseUint(seqStr, 10, 64)
	if err != nil {
		return 0, false
	}
	return seq, true
}
