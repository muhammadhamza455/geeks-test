import { RefreshCw, Sparkles } from "lucide-react";
import type { PulseSummary } from "../types";
import { formatClockTime, formatProviderBadge } from "../utils/format";

interface PulseSummaryCardProps {
  pulse: PulseSummary | null;
  isRefreshing: boolean;
  onRefresh: () => void;
}

export function PulseSummaryCard({
  pulse,
  isRefreshing,
  onRefresh,
}: PulseSummaryCardProps) {
  return (
    <section className="wp-card wp-pulse-card" aria-label="AI Pulse Summary" data-testid="pulse-summary-card">
      <div className="wp-card-header">
        <div className="wp-card-title-group">
          <span className="wp-icon-pill wp-icon-indigo" aria-hidden="true">
            <Sparkles size={15} />
          </span>
          <div>
            <h2 className="wp-card-title">AI Pulse Summary</h2>
            <p className="wp-card-caption">
              Plain-English briefing of what is trending right now
              {pulse?.wiki_filter ? ` on ${pulse.wiki_filter}` : " across Wikimedia"}
            </p>
          </div>
        </div>

        <div className="wp-pulse-actions">
          <span className="wp-provider-badge" data-testid="pulse-provider-badge">
            {formatProviderBadge(pulse?.provider ?? "heuristic")}
          </span>
          <button
            type="button"
            className="wp-btn wp-btn-secondary"
            onClick={onRefresh}
            disabled={isRefreshing}
            data-testid="refresh-pulse-btn"
          >
            <RefreshCw size={13} className={isRefreshing ? "wp-spin" : ""} />
            {isRefreshing ? "Updating..." : "Refresh"}
          </button>
        </div>
      </div>

      <p className="wp-pulse-text" data-testid="pulse-summary-text">
        {pulse?.summary ?? "Connecting to WikiPulse stream to analyze live Wikipedia trends..."}
      </p>

      {pulse && pulse.highlights.length > 0 && (
        <ul className="wp-pulse-highlights">
          {pulse.highlights.map((item, idx) => (
            <li key={`${idx}-${item.slice(0, 16)}`}>{item}</li>
          ))}
        </ul>
      )}

      <div className="wp-pulse-footer">
        <span>
          Updated {pulse ? formatClockTime(pulse.generated_at) : "--:--:--"} · Sample:{" "}
          {pulse?.edit_sample_size ?? 0} edits (5m)
        </span>
      </div>
    </section>
  );
}
