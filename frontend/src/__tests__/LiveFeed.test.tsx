import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LiveFeed } from "../components/LiveFeed";
import type { WikiEdit } from "../types";

const sampleEdits: WikiEdit[] = [
  {
    id: "edit-1",
    wiki: "enwiki",
    title: "2026 FIFA World Cup",
    title_url: "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup",
    diff_url: "https://en.wikipedia.org/w/index.php?diff=2&oldid=1",
    user: "198.51.100.42",
    is_bot: false,
    is_anon: true,
    is_minor: false,
    is_new: false,
    namespace: 0,
    comment: "",
    old_length: 140000,
    new_length: 120,
    byte_delta: -139880,
    timestamp: 1700000100,
    server_name: "en.wikipedia.org",
    vandalism: {
      flagged: true,
      severity: "high",
      confidence: 0.95,
      reason: "Near-total page blanking (-139,880 bytes, down to 120 B)",
      source: "heuristic",
    },
  },
  {
    id: "edit-2",
    wiki: "frwiki",
    title: "Paris",
    title_url: "https://fr.wikipedia.org/wiki/Paris",
    diff_url: null,
    user: "WikiEditorFR",
    is_bot: false,
    is_anon: false,
    is_minor: true,
    is_new: false,
    namespace: 0,
    comment: "Mise à jour population",
    old_length: 25000,
    new_length: 25150,
    byte_delta: 150,
    timestamp: 1700000090,
    server_name: "fr.wikipedia.org",
    vandalism: {
      flagged: false,
      severity: "none",
      confidence: 0,
      reason: "",
      source: "heuristic",
    },
  },
];

describe("LiveFeed", () => {
  it("renders edits and highlights vandalism flags with reason", () => {
    render(
      <LiveFeed
        edits={sampleEdits}
        wikiFilter={null}
        paused={false}
        queuedCount={0}
        onTogglePaused={vi.fn()}
        onSelectWiki={vi.fn()}
      />
    );

    expect(screen.getAllByTestId("live-feed-item")).toHaveLength(2);
    expect(screen.getByText("2026 FIFA World Cup")).toBeInTheDocument();
    expect(screen.getByText("Paris")).toBeInTheDocument();

    const banner = screen.getByTestId("vandalism-flag-banner");
    expect(banner).toHaveTextContent(/Possible Vandalism/i);
    expect(banner).toHaveTextContent(/Near-total page blanking/i);
  });

  it("filters to vandalism-flagged edits only and supports search", async () => {
    const user = userEvent.setup();
    render(
      <LiveFeed
        edits={sampleEdits}
        wikiFilter={null}
        paused={false}
        queuedCount={0}
        onTogglePaused={vi.fn()}
        onSelectWiki={vi.fn()}
      />
    );

    await user.click(screen.getByTestId("vandalism-only-toggle"));
    expect(screen.getAllByTestId("live-feed-item")).toHaveLength(1);
    expect(screen.getByText("2026 FIFA World Cup")).toBeInTheDocument();
    expect(screen.queryByText("Paris")).not.toBeInTheDocument();

    // Disable flagged-only and search for Paris
    await user.click(screen.getByTestId("vandalism-only-toggle"));
    await user.type(screen.getByTestId("feed-search-input"), "Paris");
    expect(screen.getAllByTestId("live-feed-item")).toHaveLength(1);
    expect(screen.getByText("Paris")).toBeInTheDocument();
  });

  it("invokes onTogglePaused and onSelectWiki callbacks", async () => {
    const user = userEvent.setup();
    const onTogglePaused = vi.fn();
    const onSelectWiki = vi.fn();

    render(
      <LiveFeed
        edits={sampleEdits}
        wikiFilter={null}
        paused={true}
        queuedCount={7}
        onTogglePaused={onTogglePaused}
        onSelectWiki={onSelectWiki}
      />
    );

    const pauseBtn = screen.getByTestId("pause-feed-btn");
    expect(pauseBtn).toHaveTextContent("Resume (+7)");
    await user.click(pauseBtn);
    expect(onTogglePaused).toHaveBeenCalledTimes(1);

    await user.click(screen.getByText("frwiki"));
    expect(onSelectWiki).toHaveBeenCalledWith("frwiki");
  });
});
