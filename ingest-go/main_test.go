package main

import (
	"strings"
	"testing"
	"time"
)

func TestLoadConfigDefaults(t *testing.T) {
	cfg := loadConfig(func(string) string { return "" })
	if cfg.ListenAddr != ":8090" || !strings.HasPrefix(cfg.UpstreamURL, "https://stream.wikimedia.org/") {
		t.Fatalf("unexpected defaults: %+v", cfg)
	}
	if cfg.Heartbeat != 15*time.Second || cfg.IdleTimeout != 60*time.Second || cfg.ReplayBuffer != 4096 {
		t.Fatalf("unexpected defaults: %+v", cfg)
	}
}

func TestLoadConfigOverridesAndRejectsBadNumbers(t *testing.T) {
	env := map[string]string{
		"INGEST_LISTEN_ADDR":          " :9999 ",
		"INGEST_REPLAY_BUFFER":        "10",
		"INGEST_SUBSCRIBER_BUFFER":    "not-a-number",
		"INGEST_IDLE_TIMEOUT_SECONDS": "1", // below the 5s minimum
	}
	cfg := loadConfig(func(k string) string { return env[k] })
	if cfg.ListenAddr != ":9999" || cfg.ReplayBuffer != 10 {
		t.Errorf("overrides not applied: %+v", cfg)
	}
	if cfg.SubscriberBuffer != 2048 || cfg.IdleTimeout != 60*time.Second {
		t.Errorf("invalid values should fall back to defaults: %+v", cfg)
	}
}

func TestBootIDHasNoDash(t *testing.T) {
	// Event IDs are "<boot>-<seq>"; a dash inside the boot ID would break parsing.
	for i := 0; i < 20; i++ {
		if id := newBootID(); id == "" || strings.Contains(id, "-") {
			t.Fatalf("bad boot id %q", id)
		}
	}
}
