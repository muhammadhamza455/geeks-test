"""Unit tests for the RollingStats sliding-window engine."""

from app.models import VandalismAssessment, WikiEdit
from app.rolling_stats import RollingStats


def _make_edit(
    edit_id: str,
    *,
    wiki: str = "enwiki",
    title: str = "Python",
    user: str = "Alice",
    ts: float = 1000.0,
    byte_delta: int = 50,
    is_bot: bool = False,
    flagged: bool = False,
) -> WikiEdit:
    return WikiEdit(
        id=edit_id,
        wiki=wiki,
        title=title,
        title_url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        user=user,
        is_bot=is_bot,
        old_length=500,
        new_length=500 + byte_delta,
        byte_delta=byte_delta,
        timestamp=ts,
        vandalism=VandalismAssessment(
            flagged=flagged,
            severity="high" if flagged else "none",
            confidence=0.9 if flagged else 0.0,
            reason="Test flag" if flagged else "",
        ),
    )


def test_rolling_epm_and_5m_window_expiration():
    stats = RollingStats()
    base = 1_700_000_000.0

    # 2 edits within last 60s, 1 edit 2 minutes ago, 1 edit 6 minutes ago (expired)
    stats.record(_make_edit("e-old", ts=base - 360), now=base)
    stats.record(_make_edit("e-2m", ts=base - 120), now=base)
    stats.record(_make_edit("e-30s", ts=base - 30), now=base)
    stats.record(_make_edit("e-5s", ts=base - 5), now=base)

    snap = stats.get_stats(now=base)
    assert snap.edits_per_minute == 2
    assert snap.edits_last_5m == 3
    assert snap.total_processed == 4

    # Advance 40s: e-30s is now 70s old (leaves 1m window), e-5s is 45s old (stays in 1m window)
    snap_later = stats.get_stats(now=base + 40)
    assert snap_later.edits_per_minute == 1
    assert snap_later.edits_last_5m == 3

    # Advance 250s from base: e-2m is now 370s old (leaves 5m window)
    snap_5m = stats.get_stats(now=base + 250)
    assert snap_5m.edits_per_minute == 0
    assert snap_5m.edits_last_5m == 2


def test_top_10_ranking_and_unique_editors():
    stats = RollingStats()
    base = 1_700_000_000.0

    # Create 12 distinct pages; Page_0 has 5 edits, Page_1 has 4, etc.
    for i in range(12):
        count = 5 if i == 0 else (4 if i == 1 else 1)
        for j in range(count):
            stats.record(
                _make_edit(
                    f"p{i}-{j}",
                    title=f"Page_{i}",
                    user=f"User_{j % 2}",
                    ts=base - 10 + j,
                    byte_delta=100 if j % 2 == 0 else -40,
                    flagged=(i == 0 and j == 0),
                ),
                now=base,
            )

    snap = stats.get_stats(now=base)
    assert len(snap.top_pages) == 10
    assert snap.top_pages[0].title == "Page_0"
    assert snap.top_pages[0].edit_count == 5
    assert snap.top_pages[0].unique_editors == 2
    assert snap.top_pages[0].net_byte_delta == 100 - 40 + 100 - 40 + 100
    assert snap.top_pages[0].flagged_count == 1
    assert snap.top_pages[1].title == "Page_1"
    assert snap.top_pages[1].edit_count == 4


def test_wiki_filtering_keeps_available_wikis_global():
    stats = RollingStats()
    base = 1_700_000_000.0

    stats.record(_make_edit("en1", wiki="enwiki", title="AI", ts=base - 5), now=base)
    stats.record(_make_edit("en2", wiki="enwiki", title="AI", ts=base - 3, is_bot=True), now=base)
    stats.record(_make_edit("fr1", wiki="frwiki", title="IA", ts=base - 2, flagged=True), now=base)

    fr_snap = stats.get_stats(wiki_filter="frwiki", now=base)
    assert fr_snap.wiki_filter == "frwiki"
    assert fr_snap.edits_per_minute == 1
    assert fr_snap.edits_last_5m == 1
    assert fr_snap.flagged_last_5m == 1
    assert len(fr_snap.top_pages) == 1
    assert fr_snap.top_pages[0].title == "IA"
    # Available wikis must still include enwiki so the UI dropdown can switch back
    wiki_names = [w.wiki for w in fr_snap.available_wikis]
    assert "enwiki" in wiki_names
    assert "frwiki" in wiki_names

    fr_recent = stats.get_recent_edits(wiki_filter="frwiki")
    assert len(fr_recent) == 1
    assert fr_recent[0].id == "fr1"


def test_update_vandalism_updates_counts_in_place():
    stats = RollingStats()
    base = 1_700_000_000.0
    stats.record(_make_edit("e1", wiki="enwiki", title="Earth", ts=base - 2, flagged=False), now=base)

    before = stats.get_stats(now=base)
    assert before.flagged_last_5m == 0

    updated = stats.update_vandalism(
        "e1",
        VandalismAssessment(
            flagged=True,
            severity="high",
            confidence=0.95,
            reason="LLM confirmed vandalism",
            source="llm",
        ),
    )
    assert updated is not None
    after = stats.get_stats(now=base)
    assert after.flagged_last_5m == 1
    assert after.top_pages[0].flagged_count == 1
    assert after.epm_history[-1].flagged_count == 1


def test_reset_clears_all_windows():
    stats = RollingStats()
    base = 1_700_000_000.0
    stats.record(_make_edit("e1", wiki="enwiki", title="Mars", ts=base - 1, flagged=True), now=base)
    assert stats.get_stats(now=base).edits_last_5m == 1

    stats.reset()
    snap = stats.get_stats(now=base)
    assert snap.edits_last_5m == 0
    assert snap.edits_per_minute == 0
    assert snap.total_processed == 0
    assert snap.top_pages == []
    assert stats.get_recent_edits() == []


def test_update_vandalism_survives_recent_eviction_within_5m_and_prunes_after():
    stats = RollingStats(max_recent=2)
    base = 1_700_000_000.0

    # Record 4 edits within 5m so e0 is evicted from max_recent=2 but remains in 5m window
    for i in range(4):
        stats.record(_make_edit(f"e{i}", title=f"Page{i}", ts=base - 10 + i), now=base)

    updated = stats.update_vandalism(
        "e0",
        VandalismAssessment(flagged=True, severity="high", confidence=0.92, reason="Late LLM flag", source="llm"),
    )
    assert updated is not None
    assert stats.get_stats(now=base).flagged_last_5m == 1

    # Advance past 5-minute window: e0 is pruned from lookup map too (no memory leak)
    stats.prune(now=base + 400)
    assert stats.update_vandalism("e0", VandalismAssessment()) is None
