"""In-memory rolling statistics engine for WikiPulse.

Tracks:
- Rolling 60-second edits-per-minute (EPM)
- 10-minute per-minute EPM history for time-series visualization
- Rolling 5-minute Top 10 most-edited pages (global or filtered by wiki)
- Active wiki breakdown and recent edits ring buffer
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
import time
from typing import Optional

from app.models import (
    DashboardStats,
    MinuteBucket,
    TopPageStat,
    VandalismAssessment,
    WikiCount,
    WikiEdit,
    normalize_wiki_filter,
)


class RollingStats:
    """Sliding-window statistics tracker for incoming Wikipedia edits."""

    WINDOW_1M_SECONDS = 60.0
    WINDOW_5M_SECONDS = 300.0
    HISTORY_MINUTES = 10

    def __init__(self, max_recent: int = 250) -> None:
        self._window_5m: deque[WikiEdit] = deque()
        self._recent: deque[WikiEdit] = deque(maxlen=max_recent)
        self._edits_by_id: dict[str, WikiEdit] = {}
        # Per-minute history over the last HISTORY_MINUTES for chart continuity
        # Maps minute_ts -> list of (wiki, flagged) tuples or compact counters
        self._bucket_counts: dict[int, Counter[str]] = defaultdict(Counter)
        self._bucket_flagged: dict[int, Counter[str]] = defaultdict(Counter)
        self._total_processed: int = 0

    @property
    def total_processed(self) -> int:
        return self._total_processed

    def reset(self) -> None:
        """Clear rolling windows and buffers (used when switching between live and replay streams)."""
        self._window_5m.clear()
        self._recent.clear()
        self._edits_by_id.clear()
        self._bucket_counts.clear()
        self._bucket_flagged.clear()
        self._total_processed = 0

    def record(self, edit: WikiEdit, now: Optional[float] = None) -> None:
        """Ingest a new WikiEdit into rolling windows and history buckets."""
        current = now if now is not None else time.time()
        self._total_processed += 1

        # Store in recent ring buffer
        self._recent.append(edit)

        # Only include in 5-minute window (and lookup index) if not already older than 5 minutes
        if edit.timestamp >= current - self.WINDOW_5M_SECONDS:
            self._window_5m.append(edit)
            self._edits_by_id[edit.id] = edit

        # Record into minute history bucket (last 10 minutes)
        minute_ts = int(edit.timestamp // 60) * 60
        cutoff_minute = int((current - self.HISTORY_MINUTES * 60) // 60) * 60
        if minute_ts >= cutoff_minute:
            self._bucket_counts[minute_ts][edit.wiki] += 1
            if edit.vandalism.flagged:
                self._bucket_flagged[minute_ts][edit.wiki] += 1

        self.prune(now=current)

    def update_vandalism(
        self, edit_id: str, assessment: VandalismAssessment
    ) -> Optional[WikiEdit]:
        """Update an existing edit's vandalism assessment (e.g. after async LLM check)."""
        edit = self._edits_by_id.get(edit_id)
        if edit is None:
            return None

        was_flagged = edit.vandalism.flagged
        edit.vandalism = assessment

        if was_flagged != assessment.flagged:
            minute_ts = int(edit.timestamp // 60) * 60
            if minute_ts in self._bucket_counts:
                delta = 1 if assessment.flagged else -1
                new_val = max(0, self._bucket_flagged[minute_ts][edit.wiki] + delta)
                self._bucket_flagged[minute_ts][edit.wiki] = new_val

        return edit

    def prune(self, now: Optional[float] = None) -> None:
        """Evict edits older than the 5-minute window and expired history buckets."""
        current = now if now is not None else time.time()
        cutoff_5m = current - self.WINDOW_5M_SECONDS

        # Timestamps are mostly monotonic, though out-of-order events can occasionally appear.
        while self._window_5m and self._window_5m[0].timestamp < cutoff_5m:
            evicted = self._window_5m.popleft()
            if self._edits_by_id.get(evicted.id) is evicted:
                self._edits_by_id.pop(evicted.id, None)

        cutoff_minute = int((current - (self.HISTORY_MINUTES + 1) * 60) // 60) * 60
        expired_minutes = [m for m in self._bucket_counts if m < cutoff_minute]
        for m in expired_minutes:
            self._bucket_counts.pop(m, None)
            self._bucket_flagged.pop(m, None)

    def get_recent_edits(
        self,
        wiki_filter: Optional[str] = None,
        limit: int = 60,
    ) -> list[WikiEdit]:
        """Return most recent edits (newest first), optionally filtered by wiki.

        The global ring buffer holds only ~10 seconds of live traffic, so for a wiki filter
        we search the full 5-minute window first; otherwise a smaller wiki's feed would be
        empty while its Top 10 table lists dozens of edits.
        """
        normalized = normalize_wiki_filter(wiki_filter)
        if normalized is None:
            return list(reversed(self._recent))[:limit]

        results: list[WikiEdit] = []
        seen: set[str] = set()
        for source in (self._window_5m, self._recent):
            for edit in reversed(source):
                if edit.wiki != normalized or edit.id in seen:
                    continue
                seen.add(edit.id)
                results.append(edit)
                if len(results) >= limit:
                    return results
        return results

    def get_flagged_edits(
        self,
        wiki_filter: Optional[str] = None,
        limit: int = 5,
        now: Optional[float] = None,
    ) -> list[WikiEdit]:
        """Return the newest vandalism-flagged edits in the 5-minute window (newest first).

        The recent-edits ring buffer only spans a few seconds on the busy live stream, so
        Pulse summaries use this to see flags from the whole window.
        """
        normalized = normalize_wiki_filter(wiki_filter)
        cutoff = (now if now is not None else time.time()) - self.WINDOW_5M_SECONDS
        results: list[WikiEdit] = []
        for edit in reversed(self._window_5m):
            if edit.timestamp < cutoff:
                continue
            if not edit.vandalism.flagged:
                continue
            if normalized and edit.wiki != normalized:
                continue
            results.append(edit)
            if len(results) >= limit:
                break
        return results

    def get_stats(
        self,
        *,
        wiki_filter: Optional[str] = None,
        now: Optional[float] = None,
        stream_mode: str = "live",
        stream_connected: bool = False,
        llm_provider: str = "heuristic",
    ) -> DashboardStats:
        """Compute rolling dashboard statistics for all wikis or a specific wiki."""
        current = now if now is not None else time.time()
        self.prune(now=current)

        normalized = normalize_wiki_filter(wiki_filter)
        cutoff_1m = current - self.WINDOW_1M_SECONDS
        cutoff_5m = current - self.WINDOW_5M_SECONDS

        wiki_counts_global: Counter[str] = Counter()
        edits_1m = 0
        edits_5m = 0
        flagged_5m = 0
        human_5m = 0
        bot_5m = 0

        # Group by (wiki, title) for Top 10 most-edited pages in last 5 minutes
        page_agg: dict[tuple[str, str], dict] = {}

        for edit in self._window_5m:
            if edit.timestamp < cutoff_5m:
                continue

            wiki_counts_global[edit.wiki] += 1

            if normalized and edit.wiki != normalized:
                continue

            edits_5m += 1
            if edit.timestamp >= cutoff_1m:
                edits_1m += 1
            if edit.vandalism.flagged:
                flagged_5m += 1
            if edit.is_bot:
                bot_5m += 1
            else:
                human_5m += 1

            key = (edit.wiki, edit.title)
            entry = page_agg.get(key)
            if entry is None:
                page_agg[key] = {
                    "title": edit.title,
                    "wiki": edit.wiki,
                    "title_url": edit.title_url,
                    "edit_count": 1,
                    "editors": {edit.user},
                    "net_byte_delta": edit.byte_delta,
                    "flagged_count": 1 if edit.vandalism.flagged else 0,
                    "last_edit_ts": edit.timestamp,
                }
            else:
                entry["edit_count"] += 1
                entry["editors"].add(edit.user)
                entry["net_byte_delta"] += edit.byte_delta
                if edit.vandalism.flagged:
                    entry["flagged_count"] += 1
                if edit.timestamp > entry["last_edit_ts"]:
                    entry["last_edit_ts"] = edit.timestamp

        # Sort Top 10 by edit count desc, then unique editors desc, then most recent edit desc
        sorted_pages = sorted(
            page_agg.values(),
            key=lambda item: (item["edit_count"], len(item["editors"]), item["last_edit_ts"]),
            reverse=True,
        )[:10]

        top_pages = [
            TopPageStat(
                title=p["title"],
                wiki=p["wiki"],
                title_url=p["title_url"],
                edit_count=p["edit_count"],
                unique_editors=len(p["editors"]),
                net_byte_delta=p["net_byte_delta"],
                flagged_count=p["flagged_count"],
                last_edit_ts=p["last_edit_ts"],
            )
            for p in sorted_pages
        ]

        # Build continuous 10-minute EPM history series
        current_minute = int(current // 60) * 60
        epm_history: list[MinuteBucket] = []
        for offset in range(self.HISTORY_MINUTES - 1, -1, -1):
            minute_ts = current_minute - offset * 60
            counts_by_wiki = self._bucket_counts.get(minute_ts)
            flagged_by_wiki = self._bucket_flagged.get(minute_ts)

            if counts_by_wiki is None:
                count = 0
            elif normalized:
                count = counts_by_wiki.get(normalized, 0)
            else:
                count = sum(counts_by_wiki.values())

            if flagged_by_wiki is None:
                fcount = 0
            elif normalized:
                fcount = flagged_by_wiki.get(normalized, 0)
            else:
                fcount = sum(flagged_by_wiki.values())

            label = datetime.fromtimestamp(minute_ts, tz=timezone.utc).strftime("%H:%M")
            epm_history.append(
                MinuteBucket(
                    minute_ts=minute_ts,
                    label=label,
                    count=count,
                    flagged_count=fcount,
                )
            )

        available_wikis = [
            WikiCount(wiki=wiki, count=count)
            for wiki, count in wiki_counts_global.most_common(30)
        ]

        return DashboardStats(
            wiki_filter=normalized,
            edits_per_minute=edits_1m,
            edits_last_5m=edits_5m,
            flagged_last_5m=flagged_5m,
            human_edits_last_5m=human_5m,
            bot_edits_last_5m=bot_5m,
            total_processed=self._total_processed,
            active_pages_5m=len(page_agg),
            top_pages=top_pages,
            epm_history=epm_history,
            available_wikis=available_wikis,
            stream_mode=stream_mode,
            stream_connected=stream_connected,
            llm_provider=llm_provider,
            updated_at=current,
        )
