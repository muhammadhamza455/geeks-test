"""FastAPI application entrypoint for WikiPulse."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

from app.ai_service import AIService, heuristic_vandalism_check
from app.config import Settings
from app.models import (
    DashboardStats,
    ModeSwitchRequest,
    PulseSummary,
    WikiEdit,
    normalize_wiki_filter,
)
from app.rolling_stats import RollingStats
from app.ws_manager import ConnectionManager
from stream_client import WikimediaStreamClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wikipulse")


class SPAStaticFiles(StaticFiles):
    """Serves the built dashboard; unknown non-API paths fall back to index.html."""

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Unknown /api/* routes must stay real 404s, not the dashboard HTML.
            # (Starlette normalizes `path` with os.path, so it uses "\\" on Windows.)
            first_segment = path.replace("\\", "/").split("/", 1)[0]
            if exc.status_code == 404 and first_segment != "api":
                return await super().get_response("index.html", scope)
            raise

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # A WebSocket to an unknown path: reject cleanly instead of erroring
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        await super().__call__(scope, receive, send)


class WikiPulseRuntime:
    """Owns the live/replay ingestion task, rolling stats, AI service, and WS broadcasts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stats = RollingStats(max_recent=250)
        self.ai = AIService(settings)
        self.ws_manager = ConnectionManager()
        self.stream_mode: str = settings.mode if settings.mode in ("live", "replay") else "live"
        self.stream_connected: bool = False
        self.started_at: float = time.time()

        self._stream_stop: asyncio.Event = asyncio.Event()
        self._stream_task: Optional[asyncio.Task[None]] = None
        self._stats_task: Optional[asyncio.Task[None]] = None
        self._pulse_task: Optional[asyncio.Task[None]] = None
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._mode_lock: asyncio.Lock = asyncio.Lock()
        self._last_pulse_processed: int = -1
        # Bumped on every live/replay switch so in-flight Pulse work from the old mode is discarded
        self._mode_epoch: int = 0

    def _set_connected(self, connected: bool) -> None:
        self.stream_connected = connected

    def current_stats(self, wiki_filter: Optional[str] = None) -> DashboardStats:
        return self.stats.get_stats(
            wiki_filter=wiki_filter,
            stream_mode=self.stream_mode,
            stream_connected=self.stream_connected,
            llm_provider=self.ai.provider_label,
        )

    async def generate_pulse(
        self, wiki_filter: Optional[str] = None, *, force: bool = False
    ) -> PulseSummary:
        """Build a Pulse summary from current stats, recent edits, and flagged edits in the 5m window.

        If the stream mode switches while a (slow) LLM call is in flight, the result describes
        the old mode's data, so it is discarded and regenerated from the fresh state.
        """
        while True:
            epoch = self._mode_epoch
            stats = self.current_stats(wiki_filter=wiki_filter)
            context = self.stats.get_recent_edits(wiki_filter=wiki_filter, limit=40)
            seen = {edit.id for edit in context}
            context.extend(
                edit
                for edit in self.stats.get_flagged_edits(wiki_filter=wiki_filter, limit=5)
                if edit.id not in seen
            )
            pulse = await self.ai.generate_pulse_summary(stats, context, force=force)
            if epoch == self._mode_epoch:
                return pulse

    async def send_fresh_snapshot(self, websocket: WebSocket, wiki_filter: Optional[str]) -> None:
        """Send a full snapshot. The slow Pulse step runs first and stats/edits are captured
        afterwards, so edits streamed to the client during the LLM call are not wiped out by
        an older edit list."""
        pulse = await self.generate_pulse(wiki_filter)
        stats = self.current_stats(wiki_filter=wiki_filter)
        recent = self.stats.get_recent_edits(wiki_filter=wiki_filter, limit=60)
        await self.ws_manager.send_snapshot(websocket, stats, recent, pulse)

    async def process_edit(self, edit: WikiEdit) -> WikiEdit:
        """Run stage-1 vandalism screening, update rolling stats, broadcast, and queue LLM verify."""
        assessment, should_verify = heuristic_vandalism_check(edit)
        edit.vandalism = assessment
        self.stats.record(edit)
        await self.ws_manager.broadcast_edit(edit)

        if should_verify and self.ai.llm_enabled:
            task = asyncio.create_task(self._verify_vandalism_bg(edit, assessment))
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

        return edit

    async def _verify_vandalism_bg(self, edit: WikiEdit, initial) -> None:
        try:
            refined = await self.ai.verify_vandalism_with_llm(edit, initial)
            if refined != initial:
                updated = self.stats.update_vandalism(edit.id, refined)
                if updated is not None:
                    await self.ws_manager.broadcast_vandalism_update(edit.id, edit.wiki, refined)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background vandalism LLM verification failed for %s", edit.id)

    async def _run_stream(self) -> None:
        client = WikimediaStreamClient(
            stream_url=self.settings.stream_url,
            user_agent=self.settings.user_agent,
            on_status_change=self._set_connected,
        )
        try:
            if self.stream_mode == "replay":
                logger.info("Starting WikiPulse in REPLAY mode using %s", self.settings.sample_file)
                iterator = client.stream_replay(
                    self.settings.sample_file,
                    interval_seconds=self.settings.replay_interval_seconds,
                    loop=True,
                    stop_event=self._stream_stop,
                )
            else:
                logger.info("Starting WikiPulse in LIVE mode from %s", self.settings.stream_url)
                iterator = client.stream_live(stop_event=self._stream_stop)

            async for edit in iterator:
                if self._stream_stop.is_set():
                    break
                try:
                    await self.process_edit(edit)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # One bad edit must never stall ingestion for the rest of the demo
                    logger.exception("Failed to process edit %s; continuing", edit.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Stream loop terminated unexpectedly")
            self.stream_connected = False

    async def _stats_broadcaster(self) -> None:
        """Push updated rolling stats to connected WebSocket clients every second."""
        try:
            while True:
                await asyncio.sleep(1.0)
                filters = self.ws_manager.active_filters()
                for wiki_filter in filters:
                    snapshot = self.current_stats(wiki_filter=wiki_filter)
                    await self.ws_manager.broadcast_stats_for_filter(wiki_filter, snapshot)
        except asyncio.CancelledError:
            raise

    async def _pulse_broadcaster(self) -> None:
        """Periodically refresh Pulse summaries for active client filters."""
        try:
            while True:
                # Poll faster right after startup/mode-switch until first pulse is populated
                sleep_secs = 2.0 if self._last_pulse_processed <= 0 else 12.0
                await asyncio.sleep(sleep_secs)
                if self.ws_manager.active_connections == 0:
                    continue
                if self.stats.total_processed == 0 or self.stats.total_processed == self._last_pulse_processed:
                    continue
                self._last_pulse_processed = self.stats.total_processed
                for wiki_filter in self.ws_manager.active_filters():
                    pulse = await self.generate_pulse(wiki_filter)
                    await self.ws_manager.broadcast_pulse_for_filter(wiki_filter, pulse)
        except asyncio.CancelledError:
            raise

    async def start(self, *, start_stream: bool = True) -> None:
        self._stats_task = asyncio.create_task(self._stats_broadcaster())
        self._pulse_task = asyncio.create_task(self._pulse_broadcaster())
        if start_stream:
            await self.start_stream_task()

    async def start_stream_task(self) -> None:
        await self.stop_stream_task()
        self._stream_stop = asyncio.Event()
        self._stream_task = asyncio.create_task(self._run_stream())

    async def stop_stream_task(self) -> None:
        self._stream_stop.set()
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None
        self.stream_connected = False

    async def switch_mode(self, new_mode: str) -> None:
        if new_mode not in ("live", "replay"):
            raise ValueError(f"Unsupported mode: {new_mode}")
        if new_mode == "replay" and not self.settings.sample_file.exists():
            raise FileNotFoundError(f"Sample file not found: {self.settings.sample_file}")
        async with self._mode_lock:
            if (
                new_mode == self.stream_mode
                and self._stream_task is not None
                and not self._stream_task.done()
            ):
                return
            await self.stop_stream_task()
            # Cancel any in-flight Stage-2 LLM checks from the previous stream mode
            pending_bg = list(self._bg_tasks)
            for task in pending_bg:
                task.cancel()
            for task in pending_bg:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._bg_tasks.clear()
            self.stream_mode = new_mode
            self.stats.reset()
            self.ai.clear_cache()
            self._mode_epoch += 1
            self._last_pulse_processed = -1
            await self.start_stream_task()
            # Push a clean snapshot to all connected clients so old-mode items clear immediately
            for ws, wiki_filter in self.ws_manager.iter_clients():
                await self.send_fresh_snapshot(ws, wiki_filter)

    async def stop(self) -> None:
        await self.stop_stream_task()
        for task in (self._stats_task, self._pulse_task, *list(self._bg_tasks)):
            if task is not None:
                task.cancel()
        for task in (self._stats_task, self._pulse_task, *list(self._bg_tasks)):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._stats_task = None
        self._pulse_task = None
        self._bg_tasks.clear()


def create_app(
    settings: Optional[Settings] = None,
    *,
    start_stream: bool = True,
) -> FastAPI:
    """Application factory so tests can inject custom settings cleanly."""
    app_settings = settings or Settings()
    runtime = WikiPulseRuntime(app_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.start(start_stream=start_stream)
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="WikiPulse API",
        version="1.0.0",
        description="Real-time Wikipedia edit stream analytics, AI trend summaries, and vandalism detection.",
        lifespan=lifespan,
    )
    app.state.runtime = runtime

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "stream_mode": runtime.stream_mode,
            "stream_connected": runtime.stream_connected,
            "llm_provider": runtime.ai.provider_label,
            "total_processed": runtime.stats.total_processed,
            "active_ws_clients": runtime.ws_manager.active_connections,
            "uptime_seconds": round(time.time() - runtime.started_at, 1),
        }

    @app.get("/api/stats", response_model=DashboardStats)
    async def get_stats(wiki: Optional[str] = Query(default=None)):
        return runtime.current_stats(wiki_filter=wiki)

    @app.get("/api/edits", response_model=list[WikiEdit])
    async def get_edits(
        wiki: Optional[str] = Query(default=None),
        limit: int = Query(default=60, ge=1, le=200),
    ):
        return runtime.stats.get_recent_edits(wiki_filter=wiki, limit=limit)

    @app.get("/api/pulse", response_model=PulseSummary)
    async def get_pulse(wiki: Optional[str] = Query(default=None)):
        return await runtime.generate_pulse(wiki)

    @app.post("/api/pulse/refresh", response_model=PulseSummary)
    async def refresh_pulse(wiki: Optional[str] = Query(default=None)):
        pulse = await runtime.generate_pulse(wiki, force=True)
        await runtime.ws_manager.broadcast_pulse_for_filter(
            normalize_wiki_filter(wiki), pulse
        )
        return pulse

    @app.post("/api/mode")
    async def switch_mode(req: ModeSwitchRequest):
        try:
            await runtime.switch_mode(req.mode)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "stream_mode": runtime.stream_mode,
            "stream_connected": runtime.stream_connected,
        }

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        wiki: Optional[str] = Query(default=None),
    ):
        normalized = normalize_wiki_filter(wiki)
        await runtime.ws_manager.connect(websocket, wiki_filter=normalized)
        try:
            # Immediately push initial snapshot so the dashboard populates without waiting
            await runtime.send_fresh_snapshot(websocket, normalized)

            while True:
                try:
                    message = await websocket.receive_json()
                except ValueError:
                    # Ignore malformed non-JSON client frames without dropping the socket
                    continue
                if not isinstance(message, dict):
                    continue
                msg_type = message.get("type")

                if msg_type == "set_filter":
                    new_filter = runtime.ws_manager.set_filter(websocket, message.get("wiki"))
                    await runtime.send_fresh_snapshot(websocket, new_filter)

                elif msg_type == "refresh_pulse":
                    cur_filter = runtime.ws_manager.get_filter(websocket)
                    f_pulse = await runtime.generate_pulse(cur_filter, force=True)
                    await runtime.ws_manager.send_json(
                        websocket,
                        {"type": "pulse", "pulse": f_pulse.model_dump(mode="json")},
                    )

                elif msg_type == "ping":
                    await runtime.ws_manager.send_json(websocket, {"type": "pong"})

        except WebSocketDisconnect:
            runtime.ws_manager.disconnect(websocket)
        except Exception:
            runtime.ws_manager.disconnect(websocket)

    # Optional single-container mode: serve the built dashboard. Mounted last so the
    # API and WebSocket routes above always take precedence.
    if app_settings.static_dir:
        static_path = Path(app_settings.static_dir)
        if (static_path / "index.html").is_file():
            app.mount("/", SPAStaticFiles(directory=static_path, html=True), name="dashboard")
            logger.info("Serving dashboard from %s", static_path)
        else:
            logger.warning("WIKIPULSE_STATIC_DIR=%s has no index.html; not serving the UI", static_path)

    return app


app = create_app()
