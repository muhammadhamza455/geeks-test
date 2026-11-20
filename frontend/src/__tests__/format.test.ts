import { describe, expect, it } from "vitest";
import {
  formatByteDelta,
  formatClockTime,
  formatMinuteLabel,
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

  it("formats chart minute buckets in local time, matching feed timestamps", () => {
    const minuteTs = 1_700_000_040; // a whole minute
    const local = new Date(minuteTs * 1000);
    const expected = `${String(local.getHours()).padStart(2, "0")}:${String(local.getMinutes()).padStart(2, "0")}`;
    expect(formatMinuteLabel(minuteTs)).toBe(expected);
  });

  it("renders times just after midnight as 00:xx, never 24:xx", () => {
    const justAfterMidnight = new Date(2026, 0, 15, 0, 5, 12).getTime() / 1000;
    expect(formatClockTime(justAfterMidnight)).toBe("00:05:12");
    expect(formatMinuteLabel(justAfterMidnight - 12)).toBe("00:05");
  });
});
