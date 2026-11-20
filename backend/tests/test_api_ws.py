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
