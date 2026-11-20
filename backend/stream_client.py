"""Wikimedia EventStreams SSE client and offline JSONL replay client.

Connects to https://stream.wikimedia.org/v2/stream/recentchange (or replays
sample_events.jsonl for offline work and deterministic tests) and yields
normalized WikiEdit models.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional
from urllib.parse import quote

import httpx

from app.models import WikiEdit

logger = logging.getLogger(__name__)

# Wikimedia introduced temporary accounts (e.g. ~2024-12345-6) to replace raw IPs
_TEMP_ACCOUNT_RE = re.compile(r"^~20\d{2}-\d+")


def is_anonymous_editor(username: str) -> bool:
    """Return True if the editor is an IP address or Wikimedia temporary account."""
    if not username:
        return True
    if _TEMP_ACCOUNT_RE.match(username):
        return True
    try:
        ipaddress.ip_address(username)
        return True
    except ValueError:
        return False


def parse_raw_event(
    raw: dict[str, Any],
    *,
    override_timestamp: Optional[float] = None,
    id_suffix: str = "",
) -> Optional[WikiEdit]:
    """Convert a raw Wikimedia recentchange JSON dict into a WikiEdit.

    Returns None for non-edit events (e.g. 'categorize', 'log', 'external') or
    malformed payloads missing required fields.
    """
    event_type = raw.get("type")
    if event_type not in ("edit", "new"):
        return None

    wiki = str(raw.get("wiki") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not wiki or not title:
        return None

    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    server_url = str(raw.get("server_url") or f"https://{raw.get('server_name', 'en.wikipedia.org')}").rstrip("/")
    server_name = str(raw.get("server_name") or "")

    title_url = str(raw.get("title_url") or meta.get("uri") or "")
    if not title_url:
        encoded_title = quote(title.replace(" ", "_"), safe="/:")
        title_url = f"{server_url}/wiki/{encoded_title}"

    diff_url = raw.get("notify_url")
    if not diff_url:
        revision = raw.get("revision") if isinstance(raw.get("revision"), dict) else {}
        new_rev = revision.get("new")
        old_rev = revision.get("old")
        if new_rev and old_rev:
            script_path = raw.get("server_script_path", "/w")
            diff_url = f"{server_url}{script_path}/index.php?diff={new_rev}&oldid={old_rev}"

    length_obj = raw.get("length") if isinstance(raw.get("length"), dict) else {}
    old_length = int(length_obj.get("old") or 0)
    new_length = int(length_obj.get("new") or 0)
    byte_delta = new_length - old_length

    raw_ts = raw.get("timestamp")
    try:
        parsed_ts = float(raw_ts) if raw_ts is not None else time.time()
    except (TypeError, ValueError):
        parsed_ts = time.time()

    timestamp = override_timestamp if override_timestamp is not None else parsed_ts

    base_id = str(meta.get("id") or raw.get("id") or f"{wiki}-{title}-{int(parsed_ts * 1000)}")
    event_id = f"{base_id}{id_suffix}" if id_suffix else base_id

    user = str(raw.get("user") or "Anonymous")
    is_bot = bool(raw.get("bot", False))

    return WikiEdit(
        id=event_id,
        wiki=wiki,
        title=title,
        title_url=title_url,
        diff_url=str(diff_url) if diff_url else None,
        user=user,
        is_bot=is_bot,
        is_anon=False if is_bot else is_anonymous_editor(user),
        is_minor=bool(raw.get("minor", False)),
        is_new=(event_type == "new"),
        namespace=int(raw.get("namespace") or 0),
        comment=str(raw.get("comment") or "").strip(),
        old_length=old_length,
        new_length=new_length,
        byte_delta=byte_delta,
        timestamp=timestamp,
        server_name=server_name,
    )


async def parse_sse_lines(
    lines: AsyncIterator[str],
    *,
    on_event_id: Optional[Callable[[str], None]] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Parse an async stream of SSE text lines and yield decoded JSON payloads."""
    data_lines: list[str] = []

    async for raw_line in lines:
        line = raw_line.rstrip("\r\n")

        if line == "":
            if data_lines:
                payload_text = "\n".join(data_lines)
                data_lines.clear()
                try:
                    parsed = json.loads(payload_text)
                    if isinstance(parsed, dict):
                        yield parsed
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed SSE JSON payload")
            continue

        if line.startswith(":"):
            # SSE comment / keep-alive heartbeat (e.g. ':ok')
            continue

        if line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
        elif line.startswith("id:"):
            event_id = line[3:].strip()
            if event_id and on_event_id is not None:
                on_event_id(event_id)

    # Flush trailing event if stream ended without final blank line
    if data_lines:
        payload_text = "\n".join(data_lines)
        try:
            parsed = json.loads(payload_text)
            if isinstance(parsed, dict):
                yield parsed
        except json.JSONDecodeError:
            pass


class WikimediaStreamClient:
    """Streams edits from Wikimedia EventStreams or replays a local JSONL recording."""

    def __init__(
        self,
        stream_url: str = "https://stream.wikimedia.org/v2/stream/recentchange",
        user_agent: str = "WikiPulse/1.0 (https://github.com/wikipulse/wikipulse; dev@wikipulse.local)",
        on_status_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.stream_url = stream_url
        self.user_agent = user_agent
        self.on_status_change = on_status_change
        self._last_event_id: Optional[str] = None

    def _notify_status(self, connected: bool) -> None:
        if self.on_status_change is not None:
            try:
                self.on_status_change(connected)
            except Exception:
                logger.exception("Error in stream status callback")

    async def stream_live(
        self,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[WikiEdit]:
        """Connect to the live Wikimedia SSE endpoint with automatic exponential backoff."""
        backoff = 1.0
        max_backoff = 30.0

        while stop_event is None or not stop_event.is_set():
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "text/event-stream",
            }
            if self._last_event_id:
                headers["Last-Event-ID"] = self._last_event_id

            try:
                timeout = httpx.Timeout(connect=15.0, read=60.0, write=10.0, pool=10.0)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    async with client.stream("GET", self.stream_url, headers=headers) as response:
                        response.raise_for_status()
                        logger.info("Connected to Wikimedia EventStreams: %s", self.stream_url)
                        self._notify_status(True)
                        backoff = 1.0

                        def _remember_id(eid: str) -> None:
                            self._last_event_id = eid

                        async for payload in parse_sse_lines(
                            response.aiter_lines(), on_event_id=_remember_id
                        ):
                            if stop_event is not None and stop_event.is_set():
                                break
                            # Use local receive wall-clock if stream timestamp drifts significantly,
                            # while keeping real event timestamp when within normal clock skew.
                            edit = parse_raw_event(payload)
                            if edit is not None:
                                now = time.time()
                                if abs(now - edit.timestamp) > 120:
                                    edit.timestamp = now
                                yield edit
            except asyncio.CancelledError:
                self._notify_status(False)
                raise
            except Exception as exc:
                self._notify_status(False)
                logger.warning(
                    "Wikimedia stream error (%s). Reconnecting in %.1fs...", exc, backoff
                )
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                        break
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, max_backoff)

        self._notify_status(False)

    async def stream_replay(
        self,
        sample_path: Path,
        *,
        interval_seconds: float = 0.35,
        loop: bool = True,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[WikiEdit]:
        """Replay recorded Wikimedia events from a JSONL file with live timestamps."""
        if not sample_path.exists():
            raise FileNotFoundError(f"Sample events file not found: {sample_path}")

        raw_events: list[dict[str, Any]] = []
        with sample_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                    if isinstance(obj, dict):
                        raw_events.append(obj)
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed JSONL line in %s", sample_path)

        if not raw_events:
            logger.warning("No valid events found in %s", sample_path)
            return

        self._notify_status(True)
        cycle = 0
        try:
            while stop_event is None or not stop_event.is_set():
                cycle += 1
                suffix = f"-r{cycle}" if cycle > 1 else ""
                for raw in raw_events:
                    if stop_event is not None and stop_event.is_set():
                        return
                    edit = parse_raw_event(
                        raw,
                        override_timestamp=time.time(),
                        id_suffix=suffix,
                    )
                    if edit is not None:
                        yield edit
                        if interval_seconds > 0:
                            if stop_event is not None:
                                try:
                                    await asyncio.wait_for(
                                        stop_event.wait(), timeout=interval_seconds
                                    )
                                    return
                                except asyncio.TimeoutError:
                                    pass
                            else:
                                await asyncio.sleep(interval_seconds)
                if not loop:
                    break
        except asyncio.CancelledError:
            raise
        finally:
            self._notify_status(False)
