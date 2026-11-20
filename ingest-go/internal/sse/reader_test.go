package sse

import (
	"errors"
	"io"
	"strings"
	"testing"
)

func readAll(t *testing.T, stream string) []Event {
	t.Helper()
	r := NewReader(strings.NewReader(stream))
	var out []Event
	for {
		ev, err := r.Next()
		if errors.Is(err, io.EOF) {
			return out
		}
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		out = append(out, ev)
	}
}

func TestReaderParsesEventsIDsCommentsAndMultilineData(t *testing.T) {
	stream := ":ok\n\n" +
		"event: message\nid: [{\"offset\":1}]\ndata: {\"a\":1}\n\n" +
		"data: line1\ndata:line2\n\n" +
		": heartbeat\n\n"
	got := readAll(t, stream)
	if len(got) != 2 {
		t.Fatalf("want 2 events, got %d: %+v", len(got), got)
	}
	if got[0].Data != `{"a":1}` || got[0].ID != `[{"offset":1}]` {
		t.Errorf("first event wrong: %+v", got[0])
	}
	if got[1].Data != "line1\nline2" {
		t.Errorf("multi-line data wrong: %q", got[1].Data)
	}
	if got[1].ID != `[{"offset":1}]` {
		t.Errorf("event id should persist across events, got %q", got[1].ID)
	}
}

func TestReaderHandlesCRLFAndBareCR(t *testing.T) {
	got := readAll(t, "data: a\r\n\r\ndata: b\r\rdata: c\n\n")
	if len(got) != 3 || got[0].Data != "a" || got[1].Data != "b" || got[2].Data != "c" {
		t.Fatalf("unexpected events: %+v", got)
	}
}

func TestReaderDiscardsTruncatedTrailingEvent(t *testing.T) {
	got := readAll(t, "data: complete\n\ndata: {\"trunc")
	if len(got) != 1 || got[0].Data != "complete" {
		t.Fatalf("truncated event must be dropped, got %+v", got)
	}
}

func TestReaderHandlesLinesLargerThanBuffer(t *testing.T) {
	big := strings.Repeat("x", 200*1024)
	got := readAll(t, "data: "+big+"\n\n")
	if len(got) != 1 || len(got[0].Data) != len(big) {
		t.Fatalf("large event not read intact (got %d events)", len(got))
	}
}

func TestReaderIgnoresFieldsWithoutData(t *testing.T) {
	got := readAll(t, "id: 7\n\nretry: 1000\n\ndata: x\n\n")
	if len(got) != 1 || got[0].ID != "7" || got[0].Data != "x" {
		t.Fatalf("unexpected events: %+v", got)
	}
}
