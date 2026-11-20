"""Pydantic schemas shared across stream ingestion, stats, AI analysis, and WebSocket API."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def normalize_wiki_filter(value: Any) -> Optional[str]:
    """Normalize a client-supplied wiki filter: non-strings and blank strings mean 'all wikis'."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


class VandalismAssessment(BaseModel):
    flagged: bool = False
    severity: Literal["none", "low", "medium", "high"] = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    source: Literal["heuristic", "llm"] = "heuristic"


class WikiEdit(BaseModel):
    id: str
    wiki: str
    title: str
    title_url: str
    diff_url: Optional[str] = None
    user: str
    is_bot: bool = False
    is_anon: bool = False
    is_minor: bool = False
    is_new: bool = False
    namespace: int = 0
    comment: str = ""
    old_length: int = 0
    new_length: int = 0
    byte_delta: int = 0
    timestamp: float
    server_name: str = ""
    vandalism: VandalismAssessment = Field(default_factory=VandalismAssessment)


class TopPageStat(BaseModel):
    title: str
    wiki: str
    title_url: str
    edit_count: int
    unique_editors: int
    net_byte_delta: int
    flagged_count: int = 0
    last_edit_ts: float


class MinuteBucket(BaseModel):
    minute_ts: int
    label: str
    count: int
    flagged_count: int = 0


class WikiCount(BaseModel):
    wiki: str
    count: int


class DashboardStats(BaseModel):
    wiki_filter: Optional[str] = None
    edits_per_minute: int = 0
    edits_last_5m: int = 0
    flagged_last_5m: int = 0
    human_edits_last_5m: int = 0
    bot_edits_last_5m: int = 0
    total_processed: int = 0
    active_pages_5m: int = 0
    top_pages: list[TopPageStat] = Field(default_factory=list)
    epm_history: list[MinuteBucket] = Field(default_factory=list)
    available_wikis: list[WikiCount] = Field(default_factory=list)
    stream_mode: str = "live"
    stream_connected: bool = False
    llm_provider: str = "heuristic"
    updated_at: float


class PulseSummary(BaseModel):
    wiki_filter: Optional[str] = None
    summary: str
    highlights: list[str] = Field(default_factory=list)
    provider: str = "heuristic"
    generated_at: float
    edit_sample_size: int = 0


class ModeSwitchRequest(BaseModel):
    mode: Literal["live", "replay"]
