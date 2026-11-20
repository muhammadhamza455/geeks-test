"""Integration tests for FastAPI REST endpoints and WebSocket real-time protocol."""

import asyncio
import time
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import WikiEdit


def _sample_edit(edit_id: str, wiki: str = "enwiki", title: str = "Artificial intelligence", **kw) -> WikiEdit:
    base = dict(
        id=edit_id,
        wiki=wiki,
        title=title,
        title_url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        user="TestEditor",
        is_bot=False,
        is_anon=False,
        comment="Updated references",
        old_length=10000,
        new_length=10250,
        byte_delta=250,
        timestamp=time.time(),
    )
    base.update(kw)
    return WikiEdit(**base)


def test_rest_endpoints_and_vandalism_flagging():
    settings = Settings(mode="replay", llm_provider="heuristic")
    app = create_app(settings=settings, start_stream=False)

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        # Inject two edits directly through the runtime pipeline
        runtime = app.state.runtime
        asyncio.run(runtime.process_edit(_sample_edit("e1", wiki="enwiki", title="World Cup")))
        asyncio.run(
            runtime.process_edit(
                _sample_edit(
                    "e2",
                    wiki="frwiki",
                    title="Paris",
                    user="198.51.100.12",
                    is_anon=True,
                    comment="",
                    old_length=40000,
                    new_length=50,
                    byte_delta=-39950,
                )
            )
        )

        stats_resp = client.get("/api/stats")
        assert stats_resp.status_code == 200
        stats = stats_resp.json()
        assert stats["edits_per_minute"] == 2
        assert stats["flagged_last_5m"] == 1
        assert len(stats["top_pages"]) == 2

        fr_stats = client.get("/api/stats?wiki=frwiki").json()
        assert fr_stats["edits_per_minute"] == 1
        assert fr_stats["top_pages"][0]["title"] == "Paris"

        edits = client.get("/api/edits?wiki=frwiki").json()
        assert len(edits) == 1
        assert edits[0]["vandalism"]["flagged"] is True
        assert edits[0]["vandalism"]["severity"] == "high"

        pulse = client.post("/api/pulse/refresh?wiki=frwiki").json()
        assert "Paris" in pulse["summary"]


def test_websocket_snapshot_filter_and_ping():
    settings = Settings(mode="replay", llm_provider="heuristic")
    app = create_app(settings=settings, start_stream=False)

    with TestClient(app) as client:
        runtime = app.state.runtime
        asyncio.run(runtime.process_edit(_sample_edit("e1", wiki="enwiki", title="Alpha")))
        asyncio.run(runtime.process_edit(_sample_edit("e2", wiki="dewiki", title="Beta")))

        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            assert snap["stats"]["edits_last_5m"] == 2
            assert len(snap["recent_edits"]) == 2
            assert snap["pulse"]["summary"]

            # Switch filter to dewiki
            ws.send_json({"type": "set_filter", "wiki": "dewiki"})
            filtered_snap = ws.receive_json()
            assert filtered_snap["type"] == "snapshot"
            assert filtered_snap["stats"]["wiki_filter"] == "dewiki"
            assert filtered_snap["stats"]["edits_last_5m"] == 1
            assert len(filtered_snap["recent_edits"]) == 1
            assert filtered_snap["recent_edits"][0]["title"] == "Beta"

            # Request pulse refresh
            ws.send_json({"type": "refresh_pulse"})
            pulse_msg = ws.receive_json()
            assert pulse_msg["type"] == "pulse"
            assert "Beta" in pulse_msg["pulse"]["summary"]

            # Malformed or non-dict frames should be ignored without closing the socket
            ws.send_text("not-json")
            ws.send_json(["unexpected", "array"])

            # Ping / pong keepalive still succeeds
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong == {"type": "pong"}


def test_websocket_ignores_bad_filter_values_without_disconnecting():
    settings = Settings(mode="replay", llm_provider="heuristic")
    app = create_app(settings=settings, start_stream=False)

    with TestClient(app) as client:
        runtime = app.state.runtime
        asyncio.run(runtime.process_edit(_sample_edit("e1", wiki="enwiki", title="Alpha")))

        with client.websocket_connect("/ws?wiki=%20%20") as ws:
            snap = ws.receive_json()
            assert snap["stats"]["wiki_filter"] is None  # blank filter means all wikis

            for bad in (123, "   ", None, {"x": 1}):
                ws.send_json({"type": "set_filter", "wiki": bad})
                filtered = ws.receive_json()
                assert filtered["type"] == "snapshot"
                assert filtered["stats"]["wiki_filter"] is None
                assert filtered["stats"]["edits_last_5m"] == 1

            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}


def test_stream_loop_survives_a_failing_edit(monkeypatch):
    """A single edit that blows up during processing must not stop ingestion."""
    import app.main as main_mod

    settings = Settings(mode="replay", llm_provider="heuristic")
    app = create_app(settings=settings, start_stream=False)
    runtime = app.state.runtime
    edits = [_sample_edit("bad"), _sample_edit("good-1"), _sample_edit("good-2")]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def stream_replay(self, *args, **kwargs):
            for edit in edits:
                yield edit

    original_process = runtime.process_edit

    async def flaky_process(edit):
        if edit.id == "bad":
            raise RuntimeError("boom")
        return await original_process(edit)

    monkeypatch.setattr(main_mod, "WikimediaStreamClient", FakeClient)
    monkeypatch.setattr(runtime, "process_edit", flaky_process)

    asyncio.run(runtime._run_stream())
    assert runtime.stats.total_processed == 2


def test_pulse_sees_flagged_edits_older_than_the_recent_slice():
    """On the busy live stream the last ~40 edits span seconds; flags from the whole 5m window must still reach Pulse."""
    settings = Settings(mode="replay", llm_provider="heuristic")
    app = create_app(settings=settings, start_stream=False)

    with TestClient(app) as client:
        runtime = app.state.runtime
        asyncio.run(
            runtime.process_edit(
                _sample_edit(
                    "vandal",
                    title="Moon landing",
                    user="203.0.113.9",
                    is_anon=True,
                    comment="",
                    old_length=60000,
                    new_length=40,
                    byte_delta=-59960,
                )
            )
        )
        for i in range(50):
            asyncio.run(runtime.process_edit(_sample_edit(f"n{i}", title=f"Page {i}")))

        assert runtime.stats.get_flagged_edits()[0].id == "vandal"
        pulse = client.post("/api/pulse/refresh").json()
        assert "Vandalism watch: 1" in pulse["summary"]
        assert '"Moon landing"' in pulse["summary"]


class _FakeWS:
    def __init__(self):
        self.msgs = []

    async def accept(self):
        pass

    async def send_json(self, payload):
        self.msgs.append(payload)


def _client_feed(msgs):
    """Replay the browser's reducer: a snapshot replaces the feed, edits are prepended."""
    feed = []
    for m in msgs:
        if m["type"] == "snapshot":
            feed = [e["id"] for e in m["recent_edits"]]
        elif m["type"] == "edit":
            feed = [m["edit"]["id"]] + feed
    return feed


def test_filter_snapshot_keeps_edits_that_arrive_during_slow_pulse():
    async def scenario():
        settings = Settings(mode="replay", llm_provider="openai", openai_api_key="k")
        runtime = create_app(settings=settings, start_stream=False).state.runtime

        async def slow_llm(*args, **kwargs):
            await asyncio.sleep(0.3)
            return '{"summary": "s", "highlights": []}'

        runtime.ai._call_llm = slow_llm
        for i in range(3):
            await runtime.process_edit(_sample_edit(f"old{i}"))
        ws = _FakeWS()
        await runtime.ws_manager.connect(ws)

        new_filter = runtime.ws_manager.set_filter(ws, "enwiki")
        task = asyncio.create_task(runtime.send_fresh_snapshot(ws, new_filter))
        await asyncio.sleep(0.05)
        for i in range(3):
            await runtime.process_edit(_sample_edit(f"new{i}"))
        await task
        return _client_feed(ws.msgs)

    feed = asyncio.run(scenario())
    assert feed[:3] == ["new2", "new1", "new0"]
    assert set(feed) == {"new0", "new1", "new2", "old0", "old1", "old2"}


def test_mode_switch_discards_in_flight_pulse_from_previous_mode():
    async def scenario():
        settings = Settings(mode="replay", llm_provider="openai", openai_api_key="k")
        runtime = create_app(settings=settings, start_stream=False).state.runtime

        async def slow_llm(system_prompt, user_prompt, *args, **kwargs):
            await asyncio.sleep(0.2)
            title = "OldModePage" if "OldModePage" in user_prompt else "NewModePage"
            return '{"summary": "Top page is %s", "highlights": []}' % title

        runtime.ai._call_llm = slow_llm
        await runtime.process_edit(_sample_edit("o1", title="OldModePage"))
        in_flight = asyncio.create_task(runtime.generate_pulse(None))
        await asyncio.sleep(0.05)

        # Simulate the switch's reset (without starting a real stream)
        runtime.stats.reset()
        runtime.ai.clear_cache()
        runtime._mode_epoch += 1
        await runtime.process_edit(_sample_edit("n1", title="NewModePage"))

        result = await in_flight
        cached = await runtime.generate_pulse(None)
        return result, cached

    result, cached = asyncio.run(scenario())
    assert "NewModePage" in result.summary
    assert "OldModePage" not in cached.summary


def test_filtered_feed_reaches_beyond_the_global_ring_buffer():
    settings = Settings(mode="replay", llm_provider="heuristic")
    runtime = create_app(settings=settings, start_stream=False).state.runtime
    asyncio.run(runtime.process_edit(_sample_edit("small-1", wiki="cywiki", title="Caerdydd")))
    for i in range(300):  # more than the 250-edit global ring buffer
        asyncio.run(runtime.process_edit(_sample_edit(f"big{i}", wiki="wikidatawiki", title=f"Q{i}")))

    assert [e.id for e in runtime.stats.get_recent_edits(wiki_filter="cywiki")] == ["small-1"]
    assert len(runtime.stats.get_recent_edits(limit=60)) == 60
