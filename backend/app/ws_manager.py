"""WebSocket connection hub managing per-client wiki filters and real-time broadcasts."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import WebSocket

from app.models import DashboardStats, PulseSummary, VandalismAssessment, WikiEdit

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks connected browser clients and each client's active wiki filter."""

    def __init__(self) -> None:
        self._clients: dict[WebSocket, Optional[str]] = {}

    @property
    def active_connections(self) -> int:
        return len(self._clients)

    def active_filters(self) -> set[Optional[str]]:
        return set(self._clients.values())

    def iter_clients(self) -> list[tuple[WebSocket, Optional[str]]]:
        return list(self._clients.items())

    async def connect(self, websocket: WebSocket, wiki_filter: Optional[str] = None) -> None:
        await websocket.accept()
        normalized = wiki_filter.strip() if wiki_filter else None
        self._clients[websocket] = normalized
        logger.info(
            "WebSocket connected (filter=%s, total=%d)", normalized or "ALL", len(self._clients)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._clients:
            self._clients.pop(websocket, None)
            logger.info("WebSocket disconnected (total=%d)", len(self._clients))

    def set_filter(self, websocket: WebSocket, wiki_filter: Optional[str]) -> Optional[str]:
        normalized = wiki_filter.strip() if wiki_filter else None
        if websocket in self._clients:
            self._clients[websocket] = normalized
        return normalized

    def get_filter(self, websocket: WebSocket) -> Optional[str]:
        return self._clients.get(websocket)

    async def send_json(self, websocket: WebSocket, payload: dict[str, Any]) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            self.disconnect(websocket)
            return False

    async def send_snapshot(
        self,
        websocket: WebSocket,
        stats: DashboardStats,
        recent_edits: list[WikiEdit],
        pulse: PulseSummary,
    ) -> None:
        await self.send_json(
            websocket,
            {
                "type": "snapshot",
                "stats": stats.model_dump(mode="json"),
                "recent_edits": [e.model_dump(mode="json") for e in recent_edits],
                "pulse": pulse.model_dump(mode="json"),
            },
        )

    async def broadcast_edit(self, edit: WikiEdit) -> None:
        if not self._clients:
            return
        payload = {"type": "edit", "edit": edit.model_dump(mode="json")}
        dead: list[WebSocket] = []
        for ws, wiki_filter in list(self._clients.items()):
            if wiki_filter and wiki_filter != edit.wiki:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_vandalism_update(
        self, edit_id: str, wiki: str, assessment: VandalismAssessment
    ) -> None:
        if not self._clients:
            return
        payload = {
            "type": "vandalism_update",
            "edit_id": edit_id,
            "vandalism": assessment.model_dump(mode="json"),
        }
        dead: list[WebSocket] = []
        for ws, wiki_filter in list(self._clients.items()):
            if wiki_filter and wiki_filter != wiki:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_stats_for_filter(
        self, wiki_filter: Optional[str], stats: DashboardStats
    ) -> None:
        if not self._clients:
            return
        payload = {"type": "stats", "stats": stats.model_dump(mode="json")}
        dead: list[WebSocket] = []
        for ws, client_filter in list(self._clients.items()):
            if client_filter != wiki_filter:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_pulse_for_filter(
        self, wiki_filter: Optional[str], pulse: PulseSummary
    ) -> None:
        if not self._clients:
            return
        payload = {"type": "pulse", "pulse": pulse.model_dump(mode="json")}
        dead: list[WebSocket] = []
        for ws, client_filter in list(self._clients.items()):
            if client_filter != wiki_filter:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
