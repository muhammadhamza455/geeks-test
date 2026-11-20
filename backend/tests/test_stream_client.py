"""Unit tests for Wikimedia SSE parsing and sample_events.jsonl replay."""

import json
from pathlib import Path

import pytest

from stream_client import (
    WikimediaStreamClient,
    is_anonymous_editor,
    parse_raw_event,
    parse_sse_lines,
)


def test_is_anonymous_editor_detects_ips_and_temp_accounts():
    assert is_anonymous_editor("192.0.2.15") is True
    assert is_anonymous_editor("2001:db8::1") is True
    assert is_anonymous_editor("~2026-441920") is True
    assert is_anonymous_editor("") is True
    assert is_anonymous_editor("RegisteredUser") is False
    assert is_anonymous_editor("ClueBot NG") is False


def test_parse_raw_event_filters_and_normalizes():
    # Non-edit events should be ignored
    assert parse_raw_event({"type": "categorize", "wiki": "enwiki", "title": "Cat:Test"}) is None
    assert parse_raw_event({"type": "log", "wiki": "enwiki", "title": "Log:Test"}) is None

    raw = {
        "meta": {"id": "abc-123", "uri": "https://en.wikipedia.org/wiki/Moon"},
        "type": "edit",
        "wiki": "enwiki",
        "title": "Moon",
        "user": "198.51.100.7",
        "bot": False,
        "minor": False,
        "namespace": 0,
        "comment": "Fixed typo",
        "timestamp": 1700000100,
        "length": {"old": 1200, "new": 1245},
        "revision": {"old": 10, "new": 11},
        "server_url": "https://en.wikipedia.org",
        "server_name": "en.wikipedia.org",
    }
    edit = parse_raw_event(raw)
    assert edit is not None
    assert edit.id == "abc-123"
    assert edit.wiki == "enwiki"
    assert edit.byte_delta == 45
    assert edit.is_anon is True
    assert edit.is_bot is False
    assert edit.diff_url == "https://en.wikipedia.org/w/index.php?diff=11&oldid=10"


def test_parse_raw_event_handles_empty_user_none_server_and_unsafe_urls():
    raw = {
        "type": "edit",
        "wiki": "enwiki",
        "title": "Test Page",
        "user": "",
        "server_name": None,
        "title_url": "javascript:alert(1)",
        "notify_url": "javascript:evil()",
        "timestamp": 1700000200,
    }
    edit = parse_raw_event(raw)
    assert edit is not None
    assert edit.user == "Anonymous"
    assert edit.is_anon is True
    assert edit.title_url == "https://en.wikipedia.org/wiki/Test_Page"
    assert edit.diff_url is None


@pytest.mark.asyncio
async def test_parse_sse_lines_handles_heartbeats_multiline_and_bad_json():
    sample_lines = [
        ":ok\n",
        "\n",
        "event: message\n",
        "id: evt-1\n",
        'data: {"type":"edit","wiki":"enwiki","title":"A"}\n',
        "\n",
        "event: message\n",
        "id: evt-2\n",
        "data: {bad json\n",
        "\n",
        "event: message\n",
        "id: evt-3\n",
        'data: {"type":"new",\n',
        'data: "wiki":"frwiki","title":"B"}\n',
        "\n",
    ]

    async def line_gen():
        for line in sample_lines:
            yield line

    seen_ids: list[str] = []
    payloads = [
        item async for item in parse_sse_lines(line_gen(), on_event_id=seen_ids.append)
    ]
    assert len(payloads) == 2
    assert payloads[0]["title"] == "A"
    assert payloads[1]["title"] == "B"
    assert seen_ids == ["evt-1", "evt-2", "evt-3"]


@pytest.mark.asyncio
async def test_stream_replay_reads_sample_events_jsonl(tmp_path: Path):
    sample_file = tmp_path / "sample.jsonl"
    records = [
        {
            "meta": {"id": "r1"},
            "type": "edit",
            "wiki": "enwiki",
            "title": "Mars",
            "user": "Editor1",
            "timestamp": 1700000001,
            "length": {"old": 100, "new": 150},
        },
        {
            "meta": {"id": "r2"},
            "type": "log",
            "wiki": "enwiki",
            "title": "IgnoredLog",
            "user": "Sysop",
            "timestamp": 1700000002,
        },
        {
            "meta": {"id": "r3"},
            "type": "new",
            "wiki": "dewiki",
            "title": "Jupiter",
            "user": "Editor2",
            "timestamp": 1700000003,
            "length": {"old": 0, "new": 400},
        },
    ]
    sample_file.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    statuses: list[bool] = []
    client = WikimediaStreamClient(on_status_change=statuses.append)
    edits = [
        e
        async for e in client.stream_replay(
            sample_file, interval_seconds=0.0, loop=False
        )
    ]
    assert len(edits) == 2
    assert edits[0].title == "Mars"
    assert edits[1].title == "Jupiter"
    assert edits[1].is_new is True
    assert statuses[0] is True
    assert statuses[-1] is False


@pytest.mark.asyncio
async def test_repo_sample_events_file_is_valid():
    repo_sample = Path(__file__).resolve().parents[2] / "sample_events.jsonl"
    assert repo_sample.exists(), "sample_events.jsonl should exist at repo root"
    client = WikimediaStreamClient()
    edits = [
        e
        async for e in client.stream_replay(
            repo_sample, interval_seconds=0.0, loop=False
        )
    ]
    assert len(edits) >= 50
    wikis = {e.wiki for e in edits}
    assert "enwiki" in wikis


def test_parse_raw_event_never_raises_on_malformed_numeric_fields():
    raw = {
        "type": "edit",
        "wiki": "enwiki",
        "title": "Odd Event",
        "user": "SomeUser",
        "namespace": "not-a-number",
        "length": {"old": "abc", "new": None},
        "timestamp": "garbage",
    }
    edit = parse_raw_event(raw)
    assert edit is not None
    assert edit.namespace == 0
    assert edit.old_length == 0 and edit.new_length == 0
    # Unexpected shapes are skipped rather than raising
    assert parse_raw_event({"type": "edit", "wiki": "enwiki", "title": "X", "meta": {"id": ["bad"]}, "length": []}) is not None


@pytest.mark.asyncio
async def test_replay_with_zero_interval_keeps_event_loop_responsive(tmp_path):
    import asyncio

    sample = tmp_path / "s.jsonl"
    sample.write_text(
        json.dumps({"type": "edit", "wiki": "enwiki", "title": "T", "meta": {"id": "1"}}) + "\n",
        encoding="utf-8",
    )
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    ticker_task = asyncio.create_task(ticker())
    client = WikimediaStreamClient()
    count = 0
    async for _ in client.stream_replay(sample, interval_seconds=0, loop=True):
        count += 1
        if count >= 200:
            break
    ticker_task.cancel()
    assert ticks > 0  # other tasks got scheduled while replay was flooding


@pytest.mark.asyncio
async def test_replay_stops_when_file_has_no_edit_events(tmp_path):
    sample = tmp_path / "logs_only.jsonl"
    sample.write_text(
        "\n".join(json.dumps({"type": "log", "wiki": "enwiki", "title": f"L{i}"}) for i in range(3)),
        encoding="utf-8",
    )
    client = WikimediaStreamClient()
    edits = [e async for e in client.stream_replay(sample, interval_seconds=0, loop=True)]
    assert edits == []  # terminates instead of spinning forever


def test_parse_raw_event_accepts_go_ingest_service_payload():
    """Contract with ingest-go: its slimmed recentchange payload must parse identically."""
    slim = {
        "meta": {"id": "abc-123", "uri": "https://en.wikipedia.org/wiki/Moon"},
        "type": "new",
        "wiki": "enwiki",
        "title": "Moon",
        "title_url": "https://en.wikipedia.org/wiki/Moon",
        "server_url": "https://en.wikipedia.org",
        "server_name": "en.wikipedia.org",
        "server_script_path": "/w",
        "user": "~2026-441920",
        "bot": False,
        "minor": False,
        "namespace": 0,
        "comment": "",
        "timestamp": 1700000100,
        "length": {"new": 1245},  # Go omits length.old for new pages
        "revision": {"new": 11},
    }
    edit = parse_raw_event(slim)
    assert edit is not None
    assert edit.id == "abc-123"
    assert edit.is_new is True
    assert edit.is_anon is True
    assert edit.old_length == 0 and edit.new_length == 1245 and edit.byte_delta == 1245
    assert edit.title_url == "https://en.wikipedia.org/wiki/Moon"
    assert edit.diff_url is None  # no old revision to diff against
