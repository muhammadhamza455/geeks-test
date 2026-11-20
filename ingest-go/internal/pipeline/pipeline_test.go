package pipeline

import (
	"encoding/json"
	"testing"
	"time"

	"wikipulse/ingest/internal/hub"
	"wikipulse/ingest/internal/sse"
)

func TestPipelineForwardsValidEditsAndCountsTheRest(t *testing.T) {
	h := hub.New("boot", 16, 16)
	sub, _ := h.Subscribe("")
	p := New(h, 100, nil)
	now := time.Unix(1_700_000_000, 0)
	p.now = func() time.Time { return now }

	if c := p.Counters(); c.LastEventAgeSeconds != -1 {
		t.Fatalf("age should be -1 before any edit, got %v", c.LastEventAgeSeconds)
	}

	edit := `{"type":"edit","wiki":"enwiki","title":"Moon","meta":{"id":"m1"},"user":"A","timestamp":1}`
	for _, data := range []string{
		edit,
		edit, // duplicate (e.g. replayed after an upstream reconnect)
		`{"type":"log","wiki":"enwiki","title":"Special:Log"}`,
		`{"type":"edit"`, // malformed
		`{"type":"new","wiki":"dewiki","title":"Neu","meta":{"id":"m2"}}`,
	} {
		p.Handle(sse.Event{Data: data})
	}

	now = now.Add(3 * time.Second)
	c := p.Counters()
	want := Counters{Received: 5, Forwarded: 2, Filtered: 1, Malformed: 1, Duplicates: 1, LastEventAgeSeconds: 3}
	if c != want {
		t.Fatalf("counters = %+v, want %+v", c, want)
	}

	first := <-sub.Messages()
	var decoded map[string]any
	if err := json.Unmarshal(first.Data, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["title"] != "Moon" || decoded["wiki"] != "enwiki" {
		t.Errorf("unexpected forwarded payload: %s", first.Data)
	}
	second := <-sub.Messages()
	if second.Seq != 2 {
		t.Errorf("second forwarded message seq = %d", second.Seq)
	}
}
