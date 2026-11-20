import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TopPagesTable } from "../components/TopPagesTable";
import type { TopPageStat } from "../types";

const samplePages: TopPageStat[] = [
  {
    title: "2026 FIFA World Cup",
    wiki: "enwiki",
    title_url: "https://en.wikipedia.org/wiki/2026_FIFA_World_Cup",
    edit_count: 9,
    unique_editors: 5,
    net_byte_delta: 620,
    flagged_count: 2,
    last_edit_ts: 1700000100,
  },
  {
    title: "Artificial intelligence",
    wiki: "enwiki",
    title_url: "https://en.wikipedia.org/wiki/Artificial_intelligence",
    edit_count: 4,
    unique_editors: 3,
    net_byte_delta: -250,
    flagged_count: 0,
    last_edit_ts: 1700000080,
  },
];

describe("TopPagesTable", () => {
  it("renders ranked top pages with edit counts and vandalism indicator", async () => {
    const user = userEvent.setup();
    const onSelectWiki = vi.fn();

    render(
      <TopPagesTable
        pages={samplePages}
        wikiFilter={null}
        onSelectWiki={onSelectWiki}
      />
    );

    const rows = screen.getAllByTestId("top-page-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("#1");
    expect(rows[0]).toHaveTextContent("2026 FIFA World Cup");
    expect(rows[0]).toHaveTextContent("2 flagged");
    expect(rows[0]).toHaveTextContent("+620 B");

    await user.click(screen.getAllByText("enwiki")[0]);
    expect(onSelectWiki).toHaveBeenCalledWith("enwiki");
  });

  it("shows empty state when there are no pages", () => {
    render(
      <TopPagesTable pages={[]} wikiFilter="dewiki" onSelectWiki={vi.fn()} />
    );
    expect(screen.getByTestId("top-pages-empty")).toBeInTheDocument();
  });
});
