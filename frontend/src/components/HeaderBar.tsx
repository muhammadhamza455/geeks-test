import { Activity, Filter, Radio, Wifi, WifiOff } from "lucide-react";
import type { ConnectionState, WikiCount } from "../types";
import { formatWikiLabel } from "../utils/format";

interface HeaderBarProps {
  connectionState: ConnectionState;
  streamMode: string;
  streamConnected: boolean;
  wikiFilter: string | null;
  availableWikis: WikiCount[];
  onWikiFilterChange: (wiki: string | null) => void;
  onSwitchMode: (mode: "live" | "replay") => void;
  isSwitchingMode: boolean;
  totalProcessed: number;
}

export function HeaderBar({
  connectionState,
  streamMode,
  streamConnected,
  wikiFilter,
  availableWikis,
  onWikiFilterChange,
  onSwitchMode,
  isSwitchingMode,
  totalProcessed,
}: HeaderBarProps) {
  const isOnline = connectionState === "connected";
  const topQuickWikis = availableWikis.slice(0, 5);

  return (
    <header className="wp-header">
      <div className="wp-header-top">
        <div className="wp-brand">
          <div className="wp-logo-badge" aria-hidden="true">
            <Activity size={20} />
          </div>
          <div>
            <div className="wp-brand-row">
              <h1 className="wp-title">WikiPulse</h1>
              <span
                className={`wp-status-pill ${
                  isOnline && streamConnected
                    ? "wp-status-live"
                    : isOnline
                    ? "wp-status-warn"
                    : "wp-status-offline"
                }`}
                data-testid="connection-status"
              >
                {isOnline ? <Wifi size={12} /> : <WifiOff size={12} />}
                <span className="wp-dot" />
                {!isOnline
                  ? connectionState === "connecting"
                    ? "Connecting..."
                    : "Reconnecting..."
                  : streamMode === "replay"
                  ? "Replay Stream"
                  : streamConnected
                  ? "Live Wikimedia SSE"
                  : "Connecting Upstream..."}
              </span>
            </div>
            <p className="wp-subtitle">
              Real-time Wikipedia editorial telemetry, AI trend briefings &amp; vandalism radar
            </p>
          </div>
        </div>

        <div className="wp-header-controls">
          <div className="wp-mode-toggle" role="group" aria-label="Stream source mode">
            <button
              type="button"
              className={`wp-mode-btn ${streamMode === "live" ? "active" : ""}`}
              onClick={() => onSwitchMode("live")}
              disabled={isSwitchingMode || streamMode === "live"}
              data-testid="mode-live-btn"
              title="Stream real-time edits from stream.wikimedia.org"
            >
              <Radio size={13} />
              Live
            </button>
            <button
              type="button"
              className={`wp-mode-btn ${streamMode === "replay" ? "active" : ""}`}
              onClick={() => onSwitchMode("replay")}
              disabled={isSwitchingMode || streamMode === "replay"}
              data-testid="mode-replay-btn"
              title="Replay recorded sample_events.jsonl offline"
            >
              Replay
            </button>
          </div>

          <label className="wp-select-wrap" htmlFor="wiki-filter-select">
            <Filter size={14} className="wp-select-icon" aria-hidden="true" />
            <select
              id="wiki-filter-select"
              className="wp-select"
              value={wikiFilter ?? ""}
              onChange={(e) => onWikiFilterChange(e.target.value || null)}
              data-testid="wiki-filter-select"
              aria-label="Filter dashboard by wiki"
            >
              <option value="">All Wikis ({availableWikis.length || "global"})</option>
              {availableWikis.map((item) => (
                <option key={item.wiki} value={item.wiki}>
                  {formatWikiLabel(item.wiki)} — {item.count}
                </option>
              ))}
              {wikiFilter && !availableWikis.some((w) => w.wiki === wikiFilter) && (
                <option value={wikiFilter}>{formatWikiLabel(wikiFilter)}</option>
              )}
            </select>
          </label>
        </div>
      </div>

      {topQuickWikis.length > 0 && (
        <div className="wp-quick-bar" aria-label="Quick wiki filters">
          <span className="wp-quick-label">Quick filter:</span>
          <button
            type="button"
            className={`wp-chip ${wikiFilter === null ? "active" : ""}`}
            onClick={() => onWikiFilterChange(null)}
            data-testid="quick-wiki-all"
          >
            All Wikis
          </button>
          {topQuickWikis.map((item) => (
            <button
              key={item.wiki}
              type="button"
              className={`wp-chip ${wikiFilter === item.wiki ? "active" : ""}`}
              onClick={() => onWikiFilterChange(item.wiki)}
              data-testid={`quick-wiki-${item.wiki}`}
            >
              {item.wiki}
              <span className="wp-chip-count">{item.count}</span>
            </button>
          ))}
          <span className="wp-processed-meta">
            {totalProcessed.toLocaleString()} edits processed this session
          </span>
        </div>
      )}
    </header>
  );
}
