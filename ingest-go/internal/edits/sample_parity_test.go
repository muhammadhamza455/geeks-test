package edits

import (
	"bufio"
	"os"
	"path/filepath"
	"testing"
)

// TestProvidedSampleMatchesPythonParser runs the provided sample_events.jsonl
// recording through Normalize. The expected counts match what the Python
// backend's parse_raw_event produces for the same file, so the Go service and
// the backend agree on every edge case (malformed JSON, non-edit types, events
// missing wiki/title, missing user/length/timestamp).
func TestProvidedSampleMatchesPythonParser(t *testing.T) {
	path := filepath.Join("..", "..", "..", "sample_events.jsonl")
	f, err := os.Open(path)
	if err != nil {
		t.Skipf("sample file not available (%v)", err)
	}
	defer f.Close()

	counts := map[Result]int{}
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	lines := 0
	for scanner.Scan() {
		if len(scanner.Bytes()) == 0 {
			continue
		}
		lines++
		_, res := Normalize(scanner.Bytes())
		counts[res]++
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}

	if lines != 2000 {
		t.Skipf("sample file has %d lines; parity counts are pinned to the provided 2,000-line recording", lines)
	}
	// 1,762 edit/new events, 3 of which lack wiki or title; 237 log/categorize; 1 invalid JSON line.
	want := map[Result]int{Accepted: 1759, Filtered: 237, Malformed: 4}
	for res, n := range want {
		if counts[res] != n {
			t.Errorf("result %v: got %d, want %d (all: %v)", res, counts[res], n, counts)
		}
	}
}
