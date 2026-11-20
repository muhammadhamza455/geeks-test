package edits

// Deduper remembers the most recent N keys and reports repeats. Wikimedia can
// re-send events after a reconnect with Last-Event-ID; this keeps them from
// being counted twice. It is not safe for concurrent use.
type Deduper struct {
	seen  map[string]struct{}
	order []string
	next  int
	full  bool
}

// NewDeduper returns a Deduper that remembers up to capacity keys (minimum 1).
func NewDeduper(capacity int) *Deduper {
	if capacity < 1 {
		capacity = 1
	}
	return &Deduper{
		seen:  make(map[string]struct{}, capacity),
		order: make([]string, capacity),
	}
}

// Seen records key and reports whether it was already among the remembered keys.
func (d *Deduper) Seen(key string) bool {
	if _, ok := d.seen[key]; ok {
		return true
	}
	if d.full {
		delete(d.seen, d.order[d.next])
	}
	d.order[d.next] = key
	d.seen[key] = struct{}{}
	d.next++
	if d.next == len(d.order) {
		d.next = 0
		d.full = true
	}
	return false
}
