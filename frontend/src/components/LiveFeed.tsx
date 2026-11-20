import { useMemo, useState } from "react";
import {
  AlertTriangle,
  Bot,
  ExternalLink,
  Pause,
  Play,
  Radio,
  Search,
  ShieldAlert,
  User,
} from "lucide-react";
import type { WikiEdit } from "../types";
import { formatByteDelta, formatClockTime } from "../utils/format";

export interface LiveFeedProps {
  edits: WikiEdit[];
  wikiFilter: string | null;
  paused: boolean;
  queuedCount: number;
  onTogglePaused: () => void;
  onSelectWiki: (wiki: string) => void;
}

export function LiveFeed({
  edits,
  wikiFilter,
  paused,
  queuedCount,
  onTogglePaused,
  onSelectWiki,
}: LiveFeedProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [flaggedOnly, setFlaggedOnly] = useState(false);

  const flaggedCount = useMemo(
    () => edits.filter((edit) => edit.vandalism.flagged).length,
    [edits]
  );

  const visibleEdits = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return edits.filter((edit) => {
      if (flaggedOnly && !edit.vandalism.flagged) return false;
      if (!q) return true;
      return (
        edit.title.toLowerCase().includes(q) ||
        edit.user.toLowerCase().includes(q) ||
        edit.comment.toLowerCase().includes(q) ||
        edit.wiki.toLowerCase().includes(q) ||
        edit.vandalism.reason.toLowerCase().includes(q)
      );
    });
  }, [edits, searchQuery, flaggedOnly]);

  return (
    <section className="wp-card wp-feed-card" aria-label="Live edit feed" data-testid="live-feed-panel">
      <div className="wp-card-header wp-feed-header">
        <div className="wp-card-title-group">
          <span className="wp-icon-pill wp-icon-cyan" aria-hidden="true">
            <Radio size={15} />
          </span>
          <div>
            <h2 className="wp-card-title">Live Edit Feed</h2>
            <p className="wp-card-caption">
              Streaming recent edits{wikiFilter ? ` for ${wikiFilter}` : " across all wikis"}
            </p>
          </div>
        </div>

        <div className="wp-feed-controls">
          <div className="wp-search-wrap">
            <Search size={13} className="wp-search-icon" aria-hidden="true" />
            <input
              type="search"
              className="wp-search-input"
              placeholder="Search title, user, summary..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="Search live edits"
              data-testid="feed-search-input"
            />
          </div>

          <button
            type="button"
            className={`wp-btn ${flaggedOnly ? "wp-btn-danger-active" : "wp-btn-secondary"}`}
            onClick={() => setFlaggedOnly((prev) => !prev)}
            data-testid="vandalism-only-toggle"
            aria-pressed={flaggedOnly}
          >
            <ShieldAlert size={13} />
            Flagged ({flaggedCount})
          </button>

          <button
            type="button"
            className={`wp-btn ${paused ? "wp-btn-amber-active" : "wp-btn-secondary"}`}
            onClick={onTogglePaused}
            data-testid="pause-feed-btn"
          >
            {paused ? <Play size={13} /> : <Pause size={13} />}
            {paused ? `Resume${queuedCount > 0 ? ` (+${queuedCount})` : ""}` : "Pause"}
          </button>
        </div>
      </div>

      {visibleEdits.length === 0 ? (
        <div className="wp-empty-state" data-testid="live-feed-empty">
          {edits.length === 0
            ? "Waiting for live Wikipedia edits to stream in..."
            : flaggedOnly
            ? "No edits currently flagged for vandalism match this view."
            : "No recent edits match your search filter."}
        </div>
      ) : (
        <ul className="wp-feed-list" data-testid="live-feed-list">
          {visibleEdits.map((edit) => {
            const { vandalism } = edit;
            const severityClass = vandalism.flagged
              ? `wp-edit-flagged wp-sev-${vandalism.severity}`
              : "";
            const deltaClass =
              edit.byte_delta > 0
                ? "wp-delta-pos"
                : edit.byte_delta < 0
                ? "wp-delta-neg"
                : "wp-delta-zero";

            return (
              <li
                key={edit.id}
                className={`wp-edit-item ${severityClass}`}
                data-testid="live-feed-item"
                data-flagged={vandalism.flagged ? "true" : "false"}
              >
                <div className="wp-edit-top">
                  <div className="wp-edit-title-wrap">
                    <button
                      type="button"
                      className="wp-wiki-tag"
                      onClick={() => onSelectWiki(edit.wiki)}
                      title={`Filter to ${edit.wiki}`}
                    >
                      {edit.wiki}
                    </button>

                    <a
                      href={edit.title_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="wp-edit-title"
                    >
                      {edit.title}
                      <ExternalLink size={11} className="wp-ext-icon" />
                    </a>

                    {edit.is_new && <span className="wp-tag wp-tag-new">NEW</span>}
                    {edit.is_minor && <span className="wp-tag wp-tag-minor">minor</span>}
                  </div>

                  <div className="wp-edit-right">
                    <span className={`wp-delta-pill ${deltaClass}`}>
                      {formatByteDelta(edit.byte_delta)} B
                    </span>
                    <time className="wp-time" dateTime={new Date(edit.timestamp * 1000).toISOString()}>
                      {formatClockTime(edit.timestamp)}
                    </time>
                  </div>
                </div>

                <div className="wp-edit-meta">
                  <span className="wp-editor-chip">
                    {edit.is_bot ? <Bot size={12} /> : <User size={12} />}
                    <span className="wp-editor-name">{edit.user}</span>
                    {edit.is_bot && <span className="wp-tag wp-tag-bot">BOT</span>}
                    {edit.is_anon && <span className="wp-tag wp-tag-anon">ANON</span>}
                  </span>

                  {edit.diff_url && (
                    <a
                      href={edit.diff_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="wp-diff-link"
                    >
                      View diff
                    </a>
                  )}
                </div>

                <p className={`wp-edit-comment ${edit.comment ? "" : "wp-comment-empty"}`}>
                  {edit.comment || "No edit summary provided"}
                </p>

                {vandalism.flagged && (
                  <div
                    className={`wp-vandalism-banner wp-banner-${vandalism.severity}`}
                    role="alert"
                    data-testid="vandalism-flag-banner"
                  >
                    <AlertTriangle size={14} className="wp-banner-icon" />
                    <div className="wp-banner-body">
                      <div className="wp-banner-head">
                        <strong>
                          {vandalism.severity === "low"
                            ? "Vandalism Rollback"
                            : "Possible Vandalism"}
                        </strong>
                        <span className="wp-banner-meta">
                          {vandalism.severity.toUpperCase()} ·{" "}
                          {Math.round(vandalism.confidence * 100)}% ·{" "}
                          {vandalism.source === "llm" ? "AI Verified" : "Heuristic"}
                        </span>
                      </div>
                      <span className="wp-banner-reason">{vandalism.reason}</span>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
