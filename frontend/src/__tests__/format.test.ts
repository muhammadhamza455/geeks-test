import { describe, expect, it } from "vitest";
import {
  formatByteDelta,
  formatProviderBadge,
  formatRelativeAge,
  formatWikiLabel,
} from "../utils/format";

describe("format utilities", () => {
  it("formats positive, negative, and zero byte deltas", () => {
    expect(formatByteDelta(1250)).toBe("+1,250");
    expect(formatByteDelta(-4200)).toBe("-4,200");
    expect(formatByteDelta(0)).toBe("0");
  });

  it("maps known wiki identifiers to readable labels", () => {
    expect(formatWikiLabel("enwiki")).toBe("English Wikipedia (enwiki)");
    expect(formatWikiLabel("wikidatawiki")).toBe("Wikidata (wikidatawiki)");
    expect(formatWikiLabel("customwiki")).toBe("customwiki");
  });

  it("formats AI provider badges cleanly", () => {
    expect(formatProviderBadge("heuristic")).toBe("Local Heuristic AI");
    expect(formatProviderBadge("openai:gpt-4o-mini")).toBe("LLM · openai:gpt-4o-mini");
  });

  it("formats relative ages accurately", () => {
    const now = 1_700_000_000_000;
    expect(formatRelativeAge(1_700_000_000 - 2, now)).toBe("just now");
    expect(formatRelativeAge(1_700_000_000 - 45, now)).toBe("45s ago");
    expect(formatRelativeAge(1_700_000_000 - 180, now)).toBe("3m ago");
  });
});
