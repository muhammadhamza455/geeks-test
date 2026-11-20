import { AlertTriangle, ExternalLink, Flame, Users } from "lucide-react";
import type { TopPageStat } from "../types";
import { formatByteDelta } from "../utils/format";

interface TopPagesTableProps {
  pages: TopPageStat[];
  wikiFilter: string | null;
  onSelectWiki: (wiki: string) => void;
}

export function TopPagesTable({ pages, wikiFilter, onSelectWiki }: TopPagesTableProps) {
  const maxEdits = pages.length > 0 ? Math.max(1, pages[0].edit_count) : 1;

  return (
    <section className="wp-card wp-top-pages-card" aria-label="Top 10 most-edited pages" data-testid="top-pages-panel">
      <div className="wp-card-header">
        <div className="wp-card-title-group">
          <span className="wp-icon-pill wp-icon-amber" aria-hidden="true">
            <Flame size={15} />
          </span>
          <div>
            <h2 className="wp-card-title">Top 10 Most-Edited Pages</h2>
            <p className="wp-card-caption">
              Rolling 5-minute window{wikiFilter ? ` · filtered to ${wikiFilter}` : " · all wikis"}
            </p>
          </div>
        </div>
        <span className="wp-count-pill">{pages.length} / 10</span>
      </div>

      {pages.length === 0 ? (
        <div className="wp-empty-state" data-testid="top-pages-empty">
          No edits recorded in the last 5 minutes for this filter yet.
        </div>
      ) : (
        <ol className="wp-top-list" data-testid="top-pages-list">
          {pages.map((page, index) => {
            const pct = Math.max(8, Math.round((page.edit_count / maxEdits) * 100));
            const deltaClass =
              page.net_byte_delta > 0
                ? "wp-delta-pos"
                : page.net_byte_delta < 0
                ? "wp-delta-neg"
                : "wp-delta-zero";

            return (
              <li
                key={`${page.wiki}:${page.title}`}
                className={`wp-top-item ${page.flagged_count > 0 ? "wp-top-item-flagged" : ""}`}
                data-testid="top-page-row"
              >
                <span className="wp-rank">#{index + 1}</span>
                <div className="wp-top-main">
                  <div className="wp-top-title-row">
                    <a
                      href={page.title_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="wp-page-link"
                      title={page.title}
                    >
                      {page.title}
                      <ExternalLink size={11} className="wp-ext-icon" />
                    </a>
                    <button
                      type="button"
                      className="wp-wiki-tag"
                      onClick={() => onSelectWiki(page.wiki)}
                      title={`Filter dashboard to ${page.wiki}`}
                    >
                      {page.wiki}
                    </button>
                  </div>

                  <div className="wp-bar-track" aria-hidden="true">
                    <div className="wp-bar-fill" style={{ width: `${pct}%` }} />
                  </div>

                  <div className="wp-top-meta">
                    <span className="wp-meta-chip">
                      <Users size={11} />
                      {page.unique_editors} editor{page.unique_editors !== 1 ? "s" : ""}
                    </span>
                    <span className={`wp-delta ${deltaClass}`}>
                      {formatByteDelta(page.net_byte_delta)} B
                    </span>
                    {page.flagged_count > 0 && (
                      <span className="wp-mini-alert">
                        <AlertTriangle size={11} />
                        {page.flagged_count} flagged
                      </span>
                    )}
                  </div>
                </div>
                <div className="wp-top-count" aria-label={`${page.edit_count} edits`}>
                  <span className="wp-top-count-num">{page.edit_count}</span>
                  <span className="wp-top-count-lbl">edits</span>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
