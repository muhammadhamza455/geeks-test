import { HeaderBar } from "./components/HeaderBar";
import { LiveFeed } from "./components/LiveFeed";
import { PulseSummaryCard } from "./components/PulseSummaryCard";
import { TopPagesTable } from "./components/TopPagesTable";
import { VelocityChart } from "./components/VelocityChart";
import { useWikiPulse } from "./hooks/useWikiPulse";

export default function App() {
  const {
    stats,
    edits,
    pulse,
    connectionState,
    wikiFilter,
    setWikiFilter,
    paused,
    togglePaused,
    queuedCount,
    isRefreshingPulse,
    refreshPulse,
    isSwitchingMode,
    switchStreamMode,
    errorBanner,
  } = useWikiPulse();

  return (
    <div className="wp-shell">
      <div className="wp-container">
        <HeaderBar
          connectionState={connectionState}
          streamMode={stats?.stream_mode ?? "live"}
          streamConnected={stats?.stream_connected ?? false}
          wikiFilter={wikiFilter}
          availableWikis={stats?.available_wikis ?? []}
          onWikiFilterChange={setWikiFilter}
          onSwitchMode={switchStreamMode}
          isSwitchingMode={isSwitchingMode}
          totalProcessed={stats?.total_processed ?? 0}
        />

        {errorBanner && (
          <div className="wp-error-banner" role="alert">
            {errorBanner}
          </div>
        )}

        <main className="wp-main">
          <PulseSummaryCard
            pulse={pulse}
            isRefreshing={isRefreshingPulse}
            onRefresh={refreshPulse}
          />

          <VelocityChart stats={stats} />

          <div className="wp-columns">
            <TopPagesTable
              pages={stats?.top_pages ?? []}
              wikiFilter={wikiFilter}
              onSelectWiki={(wiki) => setWikiFilter(wiki)}
            />

            <LiveFeed
              edits={edits}
              wikiFilter={wikiFilter}
              paused={paused}
              queuedCount={queuedCount}
              onTogglePaused={togglePaused}
              onSelectWiki={(wiki) => setWikiFilter(wiki)}
            />
          </div>
        </main>
      </div>
    </div>
  );
}
