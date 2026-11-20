import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ConnectionState,
  DashboardStats,
  PulseSummary,
  ServerMessage,
  WikiEdit,
} from "../types";

const MAX_FEED_ITEMS = 120;

function buildWsUrl(wikiFilter: string | null): string {
  const envWs = import.meta.env.VITE_WS_URL as string | undefined;
  const base =
    envWs ||
    `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
  if (!wikiFilter) return base;
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}wiki=${encodeURIComponent(wikiFilter)}`;
}

export interface UseWikiPulseResult {
  stats: DashboardStats | null;
  edits: WikiEdit[];
  pulse: PulseSummary | null;
  connectionState: ConnectionState;
  wikiFilter: string | null;
  setWikiFilter: (wiki: string | null) => void;
  paused: boolean;
  togglePaused: () => void;
  queuedCount: number;
  isRefreshingPulse: boolean;
  refreshPulse: () => void;
  isSwitchingMode: boolean;
  switchStreamMode: (mode: "live" | "replay") => Promise<void>;
  errorBanner: string | null;
}

export function useWikiPulse(): UseWikiPulseResult {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [edits, setEdits] = useState<WikiEdit[]>([]);
  const [pulse, setPulse] = useState<PulseSummary | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const [wikiFilter, setWikiFilterState] = useState<string | null>(null);
  const [paused, setPaused] = useState<boolean>(false);
  const [queuedCount, setQueuedCount] = useState<number>(0);
  const [isRefreshingPulse, setIsRefreshingPulse] = useState<boolean>(false);
  const [isSwitchingMode, setIsSwitchingMode] = useState<boolean>(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const pausedRef = useRef<boolean>(false);
  const pausedQueueRef = useRef<WikiEdit[]>([]);
  const filterRef = useRef<string | null>(null);
  const reconnectAttemptRef = useRef<number>(0);
  const reconnectTimerRef = useRef<number | null>(null);
  const pingTimerRef = useRef<number | null>(null);
  const pulseTimeoutRef = useRef<number | null>(null);

  pausedRef.current = paused;
  filterRef.current = wikiFilter;

  const togglePaused = useCallback(() => {
    // Side effects stay outside the state updater (StrictMode double-invokes updaters).
    const next = !pausedRef.current;
    pausedRef.current = next;
    setPaused(next);
    if (!next && pausedQueueRef.current.length > 0) {
      const queued = pausedQueueRef.current;
      pausedQueueRef.current = [];
      setQueuedCount(0);
      setEdits((current) => {
        const seen = new Set<string>();
        const merged: WikiEdit[] = [];
        for (const item of [...queued, ...current]) {
          if (!seen.has(item.id)) {
            seen.add(item.id);
            merged.push(item);
          }
          if (merged.length >= MAX_FEED_ITEMS) break;
        }
        return merged;
      });
    }
  }, []);

  const setWikiFilter = useCallback((nextWiki: string | null) => {
    const normalized = nextWiki && nextWiki.trim() ? nextWiki.trim() : null;
    setWikiFilterState(normalized);
    filterRef.current = normalized;
    pausedQueueRef.current = [];
    setQueuedCount(0);

    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "set_filter", wiki: normalized }));
    }
  }, []);

  const refreshPulse = useCallback(() => {
    const ws = wsRef.current;
    setIsRefreshingPulse(true);
    if (pulseTimeoutRef.current !== null) {
      window.clearTimeout(pulseTimeoutRef.current);
      pulseTimeoutRef.current = null;
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "refresh_pulse" }));
      pulseTimeoutRef.current = window.setTimeout(() => {
        pulseTimeoutRef.current = null;
        setIsRefreshingPulse(false);
      }, 6000);
    } else {
      const q = filterRef.current ? `?wiki=${encodeURIComponent(filterRef.current)}` : "";
      fetch(`/api/pulse/refresh${q}`, { method: "POST" })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((data: PulseSummary) => setPulse(data))
        .catch(() => setErrorBanner("Could not refresh Pulse summary. Backend may be offline."))
        .finally(() => setIsRefreshingPulse(false));
    }
  }, []);

  const switchStreamMode = useCallback(async (mode: "live" | "replay") => {
    setIsSwitchingMode(true);
    setErrorBanner(null);
    try {
      const resp = await fetch("/api/mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Failed to switch mode (HTTP ${resp.status})`);
      }
    } catch (err) {
      setErrorBanner(err instanceof Error ? err.message : "Failed to switch stream mode.");
    } finally {
      setIsSwitchingMode(false);
    }
  }, []);

  useEffect(() => {
    let unmounted = false;

    const cleanupTimers = () => {
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (pingTimerRef.current !== null) {
        window.clearInterval(pingTimerRef.current);
        pingTimerRef.current = null;
      }
      if (pulseTimeoutRef.current !== null) {
        window.clearTimeout(pulseTimeoutRef.current);
        pulseTimeoutRef.current = null;
      }
    };

    const connect = () => {
      if (unmounted) return;
      cleanupTimers();

      const urlFilter = filterRef.current;
      const ws = new WebSocket(buildWsUrl(urlFilter));
      wsRef.current = ws;

      // A message can be in flight for a filter the user has just left (e.g. a broadcast
      // that raced a filter change); ignore anything not addressed to the current view.
      const isForCurrentFilter = (wiki: string | null | undefined) =>
        (wiki ?? null) === filterRef.current;

      ws.onopen = () => {
        if (unmounted) {
          ws.close();
          return;
        }
        reconnectAttemptRef.current = 0;
        setConnectionState("connected");
        setErrorBanner(null);

        // The filter may have changed while this socket was still connecting (set_filter
        // could not be sent then), so re-sync the server with the current selection.
        if (filterRef.current !== urlFilter) {
          ws.send(JSON.stringify({ type: "set_filter", wiki: filterRef.current }));
        }

        pingTimerRef.current = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 25000);
      };

      ws.onmessage = (event) => {
        if (unmounted) return;
        let msg: ServerMessage;
        try {
          msg = JSON.parse(event.data) as ServerMessage;
        } catch {
          return;
        }

        if (msg.type === "snapshot") {
          if (!isForCurrentFilter(msg.stats.wiki_filter)) return;
          pausedQueueRef.current = [];
          setQueuedCount(0);
          setStats(msg.stats);
          setEdits(msg.recent_edits.slice(0, MAX_FEED_ITEMS));
          setPulse(msg.pulse);
          if (pulseTimeoutRef.current !== null) {
            window.clearTimeout(pulseTimeoutRef.current);
            pulseTimeoutRef.current = null;
          }
          setIsRefreshingPulse(false);
        } else if (msg.type === "stats") {
          if (!isForCurrentFilter(msg.stats.wiki_filter)) return;
          setStats(msg.stats);
        } else if (msg.type === "pulse") {
          if (!isForCurrentFilter(msg.pulse.wiki_filter)) return;
          setPulse(msg.pulse);
          if (pulseTimeoutRef.current !== null) {
            window.clearTimeout(pulseTimeoutRef.current);
            pulseTimeoutRef.current = null;
          }
          setIsRefreshingPulse(false);
        } else if (msg.type === "edit") {
          if (filterRef.current && msg.edit.wiki !== filterRef.current) return;
          if (pausedRef.current) {
            pausedQueueRef.current = [msg.edit, ...pausedQueueRef.current].slice(0, MAX_FEED_ITEMS);
            setQueuedCount(pausedQueueRef.current.length);
          } else {
            setEdits((prev) => {
              if (prev.some((e) => e.id === msg.edit.id)) return prev;
              return [msg.edit, ...prev].slice(0, MAX_FEED_ITEMS);
            });
          }
        } else if (msg.type === "vandalism_update") {
          setEdits((prev) =>
            prev.map((item) =>
              item.id === msg.edit_id ? { ...item, vandalism: msg.vandalism } : item
            )
          );
          if (pausedQueueRef.current.length > 0) {
            pausedQueueRef.current = pausedQueueRef.current.map((item) =>
              item.id === msg.edit_id ? { ...item, vandalism: msg.vandalism } : item
            );
          }
        }
      };

      ws.onerror = () => {
        // onclose handles reconnect backoff
      };

      ws.onclose = () => {
        // A stale socket (e.g. from StrictMode's mount/unmount/mount) must not clear timers
        // that now belong to the current connection.
        if (unmounted || wsRef.current !== ws) return;
        cleanupTimers();
        setConnectionState("reconnecting");
        const attempt = reconnectAttemptRef.current + 1;
        reconnectAttemptRef.current = attempt;
        const delayMs = Math.min(8000, 1000 * Math.pow(1.6, attempt - 1));
        reconnectTimerRef.current = window.setTimeout(connect, delayMs);
      };
    };

    connect();

    return () => {
      unmounted = true;
      cleanupTimers();
      const ws = wsRef.current;
      wsRef.current = null;
      // Closing a socket that is still CONNECTING logs a browser console error; onopen
      // already closes it once the handshake completes because `unmounted` is set.
      if (ws && ws.readyState !== WebSocket.CONNECTING) {
        ws.close();
      }
    };
  }, []);

  return {
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
  };
}
