const WIKI_LABELS: Record<string, string> = {
  enwiki: "English Wikipedia",
  frwiki: "French Wikipedia",
  dewiki: "German Wikipedia",
  eswiki: "Spanish Wikipedia",
  itwiki: "Italian Wikipedia",
  ptwiki: "Portuguese Wikipedia",
  ruwiki: "Russian Wikipedia",
  jawiki: "Japanese Wikipedia",
  zhwiki: "Chinese Wikipedia",
  arwiki: "Arabic Wikipedia",
  plwiki: "Polish Wikipedia",
  nlwiki: "Dutch Wikipedia",
  wikidatawiki: "Wikidata",
  commonswiki: "Wikimedia Commons",
  metawiki: "Meta-Wiki",
  enwiktionary: "English Wiktionary",
};

export function formatWikiLabel(wiki: string): string {
  const friendly = WIKI_LABELS[wiki];
  return friendly ? `${friendly} (${wiki})` : wiki;
}

export function formatByteDelta(delta: number): string {
  if (delta > 0) return `+${delta.toLocaleString()}`;
  if (delta < 0) return delta.toLocaleString();
  return "0";
}

export function formatClockTime(unixSeconds: number): string {
  if (!unixSeconds) return "--:--:--";
  const date = new Date(unixSeconds * 1000);
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
}

export function formatRelativeAge(unixSeconds: number, nowMs: number = Date.now()): string {
  const diffSec = Math.max(0, Math.floor(nowMs / 1000 - unixSeconds));
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const mins = Math.floor(diffSec / 60);
  if (mins < 60) return `${mins}m ago`;
  return `${Math.floor(mins / 60)}h ago`;
}

export function formatProviderBadge(provider: string): string {
  if (!provider || provider === "heuristic") {
    return "Local Heuristic AI";
  }
  return `LLM · ${provider}`;
}

/** Format a minute bucket (unix seconds) as local HH:MM so the chart matches feed timestamps. */
export function formatMinuteLabel(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}
