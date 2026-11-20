// Package edits validates and slims Wikimedia recentchange events.
//
// The output keeps the recentchange field names the WikiPulse Python backend
// already understands (backend/stream_client.py parse_raw_event), so the backend
// can consume this service's stream without any code changes. Only the fields
// the dashboard uses are kept, which cuts the payload size roughly in half.
package edits

import (
	"encoding/json"
	"strconv"
	"strings"
)

// Result classifies what Normalize did with an event.
type Result int

const (
	// Accepted means the event is a valid page edit and should be forwarded.
	Accepted Result = iota
	// Filtered means the event is valid but not a page edit (log, categorize, ...).
	Filtered
	// Malformed means the payload is not valid JSON or lacks required fields.
	Malformed
)

// Meta is the subset of the recentchange "meta" object that WikiPulse uses.
type Meta struct {
	ID  string `json:"id"`
	URI string `json:"uri,omitempty"`
}

// Sizes holds the page length before and after an edit (old is absent for new pages).
type Sizes struct {
	Old *int64 `json:"old,omitempty"`
	New *int64 `json:"new,omitempty"`
}

// Revision holds the revision IDs before and after an edit.
type Revision struct {
	Old *int64 `json:"old,omitempty"`
	New *int64 `json:"new,omitempty"`
}

// Edit is a validated, slimmed recentchange edit event.
type Edit struct {
	Meta             Meta      `json:"meta"`
	Type             string    `json:"type"`
	Wiki             string    `json:"wiki"`
	Title            string    `json:"title"`
	TitleURL         string    `json:"title_url,omitempty"`
	NotifyURL        string    `json:"notify_url,omitempty"`
	ServerURL        string    `json:"server_url,omitempty"`
	ServerName       string    `json:"server_name,omitempty"`
	ServerScriptPath string    `json:"server_script_path,omitempty"`
	User             string    `json:"user"`
	Bot              bool      `json:"bot"`
	Minor            bool      `json:"minor"`
	Namespace        int       `json:"namespace"`
	Comment          string    `json:"comment"`
	Timestamp        int64     `json:"timestamp"`
	Length           *Sizes    `json:"length,omitempty"`
	Revision         *Revision `json:"revision,omitempty"`
}

// Key returns a stable identity used for de-duplication.
func (e *Edit) Key() string {
	if e.Meta.ID != "" {
		return e.Meta.ID
	}
	return e.Wiki + "|" + e.Title + "|" + strconv.FormatInt(e.Timestamp, 10)
}

// Normalize parses one raw recentchange payload. Only "edit" and "new" events are
// accepted; everything else is Filtered, and unparseable or incomplete payloads
// are Malformed.
func Normalize(raw []byte) (Edit, Result) {
	var probe struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(raw, &probe); err != nil {
		return Edit{}, Malformed
	}
	if probe.Type != "edit" && probe.Type != "new" {
		return Edit{}, Filtered
	}

	var e Edit
	if err := json.Unmarshal(raw, &e); err != nil {
		return Edit{}, Malformed
	}

	e.Wiki = strings.TrimSpace(e.Wiki)
	e.Title = strings.TrimSpace(e.Title)
	if e.Wiki == "" || e.Title == "" {
		return Edit{}, Malformed
	}

	e.User = strings.TrimSpace(e.User)
	e.Comment = strings.TrimSpace(e.Comment)
	e.ServerName = strings.TrimSpace(e.ServerName)

	// Defence in depth: only http(s) links may reach the browser as hrefs.
	e.TitleURL = safeHTTPURL(e.TitleURL)
	e.NotifyURL = safeHTTPURL(e.NotifyURL)
	e.ServerURL = safeHTTPURL(e.ServerURL)
	e.Meta.URI = safeHTTPURL(e.Meta.URI)

	if e.Length != nil && e.Length.Old == nil && e.Length.New == nil {
		e.Length = nil
	}
	if e.Revision != nil && e.Revision.Old == nil && e.Revision.New == nil {
		e.Revision = nil
	}
	return e, Accepted
}

// Encode returns the compact JSON form forwarded to subscribers.
func (e *Edit) Encode() ([]byte, error) {
	return json.Marshal(e)
}

func safeHTTPURL(u string) string {
	u = strings.TrimSpace(u)
	if strings.HasPrefix(u, "https://") || strings.HasPrefix(u, "http://") {
		return u
	}
	return ""
}
