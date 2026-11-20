import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWikiPulse } from "../hooks/useWikiPulse";
import type { DashboardStats, PulseSummary, ServerMessage, WikiEdit } from "../types";

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readyState = MockWebSocket.CONNECTING;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  // Test helpers simulating the server side
  serverOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  serverSend(msg: ServerMessage) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }

  serverDrop() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
}

function makeEdit(id: string, overrides: Partial<WikiEdit> = {}): WikiEdit {
  return {
    id,
    wiki: "enwiki",
    title: `Page ${id}`,
    title_url: `https://en.wikipedia.org/wiki/Page_${id}`,
    diff_url: null,
    user: "Editor",
    is_bot: false,
    is_anon: false,
    is_minor: false,
    is_new: false,
    namespace: 0,
    comment: "",
    old_length: 100,
    new_length: 110,
    byte_delta: 10,
    timestamp: 1700000000,
    server_name: "en.wikipedia.org",
    vandalism: { flagged: false, severity: "none", confidence: 0, reason: "", source: "heuristic" },
    ...overrides,
  };
}

const stats = { edits_per_minute: 7, stream_mode: "live", available_wikis: [] } as unknown as DashboardStats;
const pulse = { summary: "Quiet so far.", highlights: [], provider: "heuristic" } as unknown as PulseSummary;

const latestSocket = () => MockWebSocket.instances[MockWebSocket.instances.length - 1];

describe("useWikiPulse", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("applies snapshot, prepends new edits without duplicates, and patches vandalism updates", () => {
    const { result } = renderHook(() => useWikiPulse());
    const ws = latestSocket();

    act(() => {
      ws.serverOpen();
      ws.serverSend({ type: "snapshot", stats, recent_edits: [makeEdit("a")], pulse });
    });
    expect(result.current.connectionState).toBe("connected");
    expect(result.current.stats?.edits_per_minute).toBe(7);
    expect(result.current.pulse?.summary).toBe("Quiet so far.");

    act(() => {
      ws.serverSend({ type: "edit", edit: makeEdit("b") });
      ws.serverSend({ type: "edit", edit: makeEdit("b") }); // duplicate is ignored
    });
    expect(result.current.edits.map((e) => e.id)).toEqual(["b", "a"]);

    act(() => {
      ws.serverSend({
        type: "vandalism_update",
        edit_id: "a",
        vandalism: { flagged: true, severity: "high", confidence: 0.9, reason: "Blanked page", source: "llm" },
      });
    });
    const patched = result.current.edits.find((e) => e.id === "a");
    expect(patched?.vandalism.flagged).toBe(true);
    expect(patched?.vandalism.source).toBe("llm");
  });

  it("queues edits while paused and merges them in order on resume", () => {
    const { result } = renderHook(() => useWikiPulse());
    const ws = latestSocket();
    act(() => {
      ws.serverOpen();
      ws.serverSend({ type: "snapshot", stats, recent_edits: [makeEdit("a")], pulse });
    });

    act(() => result.current.togglePaused());
    expect(result.current.paused).toBe(true);

    act(() => {
      ws.serverSend({ type: "edit", edit: makeEdit("b") });
      ws.serverSend({ type: "edit", edit: makeEdit("c") });
    });
    expect(result.current.edits.map((e) => e.id)).toEqual(["a"]);
    expect(result.current.queuedCount).toBe(2);

    act(() => result.current.togglePaused());
    expect(result.current.paused).toBe(false);
    expect(result.current.queuedCount).toBe(0);
    expect(result.current.edits.map((e) => e.id)).toEqual(["c", "b", "a"]);
  });

  it("sends set_filter over an open socket and reconnects with the active filter", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useWikiPulse());
    const first = latestSocket();
    act(() => first.serverOpen());

    act(() => result.current.setWikiFilter(" dewiki "));
    expect(result.current.wikiFilter).toBe("dewiki");
    expect(first.sent).toContainEqual({ type: "set_filter", wiki: "dewiki" });

    act(() => first.serverDrop());
    expect(result.current.connectionState).toBe("reconnecting");

    act(() => {
      vi.advanceTimersByTime(1500);
    });
    const second = latestSocket();
    expect(second).not.toBe(first);
    expect(second.url).toContain("wiki=dewiki");
  });

  it("does not close a still-connecting socket on unmount (avoids browser console error)", () => {
    const { unmount } = renderHook(() => useWikiPulse());
    const ws = latestSocket();
    const closeSpy = vi.spyOn(ws, "close");

    unmount();
    expect(closeSpy).not.toHaveBeenCalled();

    // Once the handshake completes, the stale socket closes itself
    ws.serverOpen();
    expect(closeSpy).toHaveBeenCalledTimes(1);
  });

  it("re-sends the filter on open when it changed while the socket was connecting", () => {
    const { result } = renderHook(() => useWikiPulse());
    const ws = latestSocket();
    expect(ws.url).not.toContain("wiki=");

    act(() => result.current.setWikiFilter("enwiki")); // socket not open yet: nothing sent
    expect(ws.sent).toEqual([]);

    act(() => ws.serverOpen());
    expect(ws.sent).toContainEqual({ type: "set_filter", wiki: "enwiki" });
  });

  it("ignores in-flight messages addressed to a filter the user has left", () => {
    const { result } = renderHook(() => useWikiPulse());
    const ws = latestSocket();
    act(() => {
      ws.serverOpen();
      ws.serverSend({ type: "snapshot", stats, recent_edits: [], pulse });
    });

    act(() => result.current.setWikiFilter("dewiki"));
    act(() => {
      // Late messages for the previous "all wikis" view
      ws.serverSend({ type: "stats", stats: { ...stats, edits_per_minute: 999, wiki_filter: null } });
      ws.serverSend({ type: "edit", edit: makeEdit("en-1", { wiki: "enwiki" }) });
    });
    expect(result.current.stats?.edits_per_minute).toBe(7);
    expect(result.current.edits).toEqual([]);

    act(() => {
      ws.serverSend({
        type: "snapshot",
        stats: { ...stats, edits_per_minute: 3, wiki_filter: "dewiki" },
        recent_edits: [makeEdit("de-1", { wiki: "dewiki" })],
        pulse: { ...pulse, wiki_filter: "dewiki" },
      });
      ws.serverSend({ type: "edit", edit: makeEdit("de-2", { wiki: "dewiki" }) });
    });
    expect(result.current.stats?.edits_per_minute).toBe(3);
    expect(result.current.edits.map((e) => e.id)).toEqual(["de-2", "de-1"]);
  });
});
