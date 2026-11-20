package edits

import (
	"encoding/json"
	"fmt"
	"testing"
)

const sampleEdit = `{
  "$schema": "/mediawiki/recentchange/1.0.0",
  "meta": {"uri": "https://en.wikipedia.org/wiki/Moon", "id": "abc-123", "domain": "en.wikipedia.org", "stream": "mediawiki.recentchange"},
  "id": 1, "type": "edit", "namespace": 0, "title": " Moon ", "title_url": "https://en.wikipedia.org/wiki/Moon",
  "comment": "Fixed typo", "parsedcomment": "<b>Fixed</b> typo", "timestamp": 1700000100,
  "user": "198.51.100.7", "bot": false, "minor": true, "patrolled": true,
  "length": {"old": 1200, "new": 1245}, "revision": {"old": 10, "new": 11},
  "notify_url": "https://en.wikipedia.org/w/index.php?diff=11&oldid=10",
  "server_url": "https://en.wikipedia.org", "server_name": "en.wikipedia.org",
  "server_script_path": "/w", "wiki": "enwiki"
}`

func TestNormalizeAcceptsEditAndSlimsPayload(t *testing.T) {
	e, res := Normalize([]byte(sampleEdit))
	if res != Accepted {
		t.Fatalf("want Accepted, got %v", res)
	}
	if e.Title != "Moon" || e.Wiki != "enwiki" || !e.Minor || e.Namespace != 0 {
		t.Errorf("fields not parsed/trimmed: %+v", e)
	}
	if *e.Length.Old != 1200 || *e.Length.New != 1245 || *e.Revision.New != 11 {
		t.Errorf("length/revision wrong: %+v %+v", e.Length, e.Revision)
	}

	out, err := e.Encode()
	if err != nil {
		t.Fatal(err)
	}
	if len(out) >= len(sampleEdit) {
		t.Errorf("encoded payload should be slimmer: %d >= %d", len(out), len(sampleEdit))
	}
	var m map[string]any
	if err := json.Unmarshal(out, &m); err != nil {
		t.Fatal(err)
	}
	for _, dropped := range []string{"parsedcomment", "patrolled", "$schema", "id"} {
		if _, ok := m[dropped]; ok {
			t.Errorf("field %q should be dropped", dropped)
		}
	}
	// Fields the Python backend's parse_raw_event relies on must survive.
	for _, kept := range []string{"meta", "type", "wiki", "title", "title_url", "notify_url", "server_url", "server_name", "server_script_path", "user", "bot", "minor", "namespace", "comment", "timestamp", "length", "revision"} {
		if _, ok := m[kept]; !ok {
			t.Errorf("field %q must be kept", kept)
		}
	}
}

func TestNormalizeFiltersNonEditEvents(t *testing.T) {
	for _, typ := range []string{"log", "categorize", "external", ""} {
		raw := fmt.Sprintf(`{"type": %q, "wiki": "enwiki", "title": "X", "namespace": "weird"}`, typ)
		if _, res := Normalize([]byte(raw)); res != Filtered {
			t.Errorf("type %q: want Filtered, got %v", typ, res)
		}
	}
}

func TestNormalizeRejectsMalformedPayloads(t *testing.T) {
	cases := map[string]string{
		"not json":         `{"type": "edit"`,
		"missing wiki":     `{"type": "edit", "title": "X"}`,
		"blank title":      `{"type": "new", "wiki": "enwiki", "title": "   "}`,
		"wrong type":       `{"type": "edit", "wiki": "enwiki", "title": "X", "namespace": "zero"}`,
		"array not object": `[1, 2]`,
	}
	for name, raw := range cases {
		if _, res := Normalize([]byte(raw)); res != Malformed {
			t.Errorf("%s: want Malformed, got %v", name, res)
		}
	}
}

func TestNormalizeStripsUnsafeURLs(t *testing.T) {
	raw := `{"type": "edit", "wiki": "enwiki", "title": "X",
	  "title_url": "javascript:alert(1)", "notify_url": "data:text/html,x",
	  "server_url": "ftp://example.org", "meta": {"id": "1", "uri": "  https://ok.example/x  "}}`
	e, res := Normalize([]byte(raw))
	if res != Accepted {
		t.Fatalf("want Accepted, got %v", res)
	}
	if e.TitleURL != "" || e.NotifyURL != "" || e.ServerURL != "" {
		t.Errorf("unsafe URLs must be removed: %+v", e)
	}
	if e.Meta.URI != "https://ok.example/x" {
		t.Errorf("safe URL should be kept and trimmed, got %q", e.Meta.URI)
	}
}

func TestNormalizeNewPageWithoutOldLength(t *testing.T) {
	e, res := Normalize([]byte(`{"type": "new", "wiki": "dewiki", "title": "Neu", "length": {"new": 300}, "revision": {}}`))
	if res != Accepted {
		t.Fatalf("want Accepted, got %v", res)
	}
	if e.Length == nil || e.Length.Old != nil || *e.Length.New != 300 {
		t.Errorf("length wrong: %+v", e.Length)
	}
	if e.Revision != nil {
		t.Errorf("empty revision should be dropped, got %+v", e.Revision)
	}
}

func TestKeyFallsBackWhenMetaIDMissing(t *testing.T) {
	e := Edit{Wiki: "enwiki", Title: "X", Timestamp: 5}
	if got := e.Key(); got != "enwiki|X|5" {
		t.Errorf("fallback key wrong: %q", got)
	}
	e.Meta.ID = "id-1"
	if got := e.Key(); got != "id-1" {
		t.Errorf("meta id should win: %q", got)
	}
}

func TestDeduperRemembersRecentKeysAndEvictsOldest(t *testing.T) {
	d := NewDeduper(2)
	if d.Seen("a") || d.Seen("b") {
		t.Fatal("first sightings must not be duplicates")
	}
	if !d.Seen("a") {
		t.Fatal("repeat of a must be a duplicate")
	}
	d.Seen("c") // evicts "a" (oldest inserted)
	if d.Seen("a") {
		t.Fatal("a was evicted and should be new again")
	}
	if !d.Seen("c") {
		t.Fatal("c should still be remembered")
	}
}
