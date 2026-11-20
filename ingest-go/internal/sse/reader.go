// Package sse parses a text/event-stream (Server-Sent Events) body.
//
// It implements the parts of the WHATWG event-stream format that Wikimedia
// EventStreams uses: "data" (possibly multi-line), "id", comment lines used as
// heartbeats, and LF / CRLF / CR line endings.
package sse

import (
	"bufio"
	"errors"
	"io"
	"strings"
)

// Event is one dispatched server-sent event.
type Event struct {
	// ID is the last event ID seen on the stream (it persists across events, per the spec).
	ID string
	// Data is the event payload with multi-line data fields joined by "\n".
	Data string
}

// Reader reads events from an SSE stream.
type Reader struct {
	br     *bufio.Reader
	lastID string
	// pendingCR is set when the previous line ended in a bare '\r' so a following
	// '\n' (CRLF split across reads) is not treated as an extra empty line.
	pendingCR bool
}

// NewReader returns a Reader for the given stream body.
func NewReader(r io.Reader) *Reader {
	return &Reader{br: bufio.NewReaderSize(r, 64*1024)}
}

// Next returns the next event that carries data. It returns io.EOF when the
// stream ends; an incomplete trailing event (no terminating blank line) is
// discarded, as the spec requires, because it may be truncated.
func (r *Reader) Next() (Event, error) {
	var data strings.Builder
	hasData := false

	for {
		line, err := r.readLine()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return Event{}, io.EOF
			}
			return Event{}, err
		}

		if line == "" {
			if hasData {
				return Event{ID: r.lastID, Data: data.String()}, nil
			}
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue // comment / heartbeat
		}

		field, value := line, ""
		if i := strings.IndexByte(line, ':'); i >= 0 {
			field, value = line[:i], line[i+1:]
			value = strings.TrimPrefix(value, " ")
		}

		switch field {
		case "data":
			if hasData {
				data.WriteByte('\n')
			}
			data.WriteString(value)
			hasData = true
		case "id":
			if !strings.ContainsRune(value, 0) {
				r.lastID = value
			}
		}
		// "event" and "retry" are not needed by WikiPulse and are ignored.
	}
}

// LastID returns the most recent event ID seen on the stream.
func (r *Reader) LastID() string { return r.lastID }

// readLine returns one line without its terminator, accepting LF, CRLF and CR.
func (r *Reader) readLine() (string, error) {
	var sb strings.Builder
	for {
		b, err := r.br.ReadByte()
		if err != nil {
			if errors.Is(err, io.EOF) && sb.Len() > 0 {
				// Unterminated final line: treat the stream as truncated.
				return "", io.EOF
			}
			return "", err
		}
		if r.pendingCR {
			r.pendingCR = false
			if b == '\n' {
				continue
			}
		}
		switch b {
		case '\n':
			return sb.String(), nil
		case '\r':
			r.pendingCR = true
			return sb.String(), nil
		default:
			sb.WriteByte(b)
		}
	}
}
