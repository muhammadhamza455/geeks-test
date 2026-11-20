"""Single-container mode: FastAPI serves the built dashboard next to the API and WebSocket."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _client(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><div id='root'></div>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    settings = Settings(mode="replay", llm_provider="heuristic", static_dir=str(tmp_path))
    return TestClient(create_app(settings=settings, start_stream=False))


def test_serves_dashboard_assets_and_spa_fallback(tmp_path):
    with _client(tmp_path) as client:
        index = client.get("/")
        assert index.status_code == 200 and "id='root'" in index.text

        asset = client.get("/assets/app.js")
        assert asset.status_code == 200 and "console.log" in asset.text

        deep_link = client.get("/some/client/route")
        assert deep_link.status_code == 200 and "id='root'" in deep_link.text


def test_api_and_websocket_still_take_precedence(tmp_path):
    with _client(tmp_path) as client:
        health = client.get("/api/health")
        assert health.status_code == 200 and health.json()["status"] == "ok"

        missing_api = client.get("/api/does-not-exist")
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"].startswith("application/json")

        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_ui_is_not_served_without_static_dir(tmp_path):
    settings = Settings(mode="replay", llm_provider="heuristic", static_dir="")
    with TestClient(create_app(settings=settings, start_stream=False)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200


def test_missing_index_html_disables_ui_without_crashing(tmp_path):
    settings = Settings(mode="replay", llm_provider="heuristic", static_dir=str(tmp_path / "nope"))
    with TestClient(create_app(settings=settings, start_stream=False)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200
