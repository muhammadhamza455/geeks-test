"""AI analysis service for WikiPulse: Pulse trend summaries and Vandalism detection.

Architecture:
- Two-stage vandalism pipeline:
  1. Fast deterministic heuristic pre-screener (<1ms, zero API cost) scores every edit
     using real Wikipedia anti-vandalism signals (page blanking, large unattributed deletions,
     anonymous/temp-account risk, vandalism/revert markers, spam/profanity cues).
  2. When an LLM API key is configured (OpenAI, Anthropic, or Gemini), suspicious candidates
     are asynchronously verified by the LLM within a configurable per-minute call budget.
- Pulse summary:
  Generates a concise plain-English briefing of current Wikipedia trends, cached per wiki
  filter to respect API rate and spending limits, with a rich data-driven fallback when
  running without an API key.
"""

from __future__ import annotations

from collections import deque
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from app.config import Settings
from app.models import DashboardStats, PulseSummary, VandalismAssessment, WikiEdit

logger = logging.getLogger(__name__)

# Word-boundary regexes for common vandalism/profanity/test-edit signals in edit summaries
_PROFANITY_OR_TEST_RE = re.compile(
    r"\b(poop|penis|fuck\w*|shit\w*|bitch\w*|asshole|lololol+|hahaha+|hacked|test\s*hack|ur\s*mom|skibidi|asdfgh)\b",
    re.IGNORECASE,
)
_VANDALISM_ROLLBACK_RE = re.compile(
    r"\b(rvv|vandal\w*|blanking|lta|unconstructive|revert(?:ed|ing)?\s+(?:possible\s+)?vandalism|anti-?vandal)\b",
    re.IGNORECASE,
)
_BENIGN_DELETE_RE = re.compile(
    r"\b(redirect|merge|archived?|clean\s*up|copyedit|split|move|bot|template|duplicate|outdated|rm\s+copyvio|copyright|afd|csd)\b",
    re.IGNORECASE,
)
_ANTI_VANDAL_USERS = {"cluebot ng", "salebot", "huggle", "swviewer", "antivandal"}


def _extract_json_object(raw_text: str) -> Optional[dict[str, Any]]:
    """Parse a JSON object from LLM text output, handling markdown code fences."""
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` wrappers
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        # Try finding the outermost {...} substring
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
        return None


def heuristic_vandalism_check(edit: WikiEdit) -> tuple[VandalismAssessment, bool]:
    """Synchronously evaluate an edit for vandalism cues.

    Returns:
        (assessment, should_verify_with_llm)
    """
    comment = edit.comment or ""
    comment_lower = comment.lower()
    user_lower = edit.user.lower()

    # 1. Explicit vandalism rollback / anti-vandalism bot activity (signals active vandalism on the page)
    if _VANDALISM_ROLLBACK_RE.search(comment) or (
        user_lower in _ANTI_VANDAL_USERS and ("revert" in comment_lower or "rv" in comment_lower)
    ):
        return (
            VandalismAssessment(
                flagged=True,
                severity="low",
                confidence=0.82,
                reason="Vandalism rollback detected — recent vandalism on this page was just reverted.",
                source="heuristic",
            ),
            False,
        )

    # 2. Skip normal bot maintenance edits unless matched above
    if edit.is_bot:
        return VandalismAssessment(), False

    reasons: list[str] = []
    score = 0.0
    is_main_article = edit.namespace == 0
    has_benign_delete_reason = bool(_BENIGN_DELETE_RE.search(comment))

    # 3. Severe page blanking
    if (
        edit.old_length >= 1000
        and edit.new_length < 250
        and edit.byte_delta <= -800
        and not has_benign_delete_reason
    ):
        score += 0.85
        reasons.append(
            f"Near-total page blanking ({edit.byte_delta:,} bytes, down to {edit.new_length:,} B)"
        )
    # 4. Large unattributed content removal
    elif edit.byte_delta <= -1500 and not has_benign_delete_reason:
        if not comment:
            score += 0.72 if edit.is_anon else 0.55
            reasons.append(
                f"Large deletion ({edit.byte_delta:,} bytes) with no edit summary"
            )
        elif edit.is_anon and edit.byte_delta <= -3000:
            score += 0.55
            reasons.append(
                f"Major content removal ({edit.byte_delta:,} bytes) by anonymous editor"
            )
    elif (
        edit.is_anon
        and is_main_article
        and edit.byte_delta <= -500
        and not comment
        and not has_benign_delete_reason
    ):
        score += 0.58
        reasons.append(
            f"Anonymous removal of {abs(edit.byte_delta):,} bytes with no edit summary"
        )

    # 5. Profanity / gibberish / prank markers in edit summary
    if _PROFANITY_OR_TEST_RE.search(comment):
        score += 0.75
        reasons.append("Suspicious prank, test, or profane wording in edit summary")

    # 6. Massive unexplained insertion by anonymous/temp editor on main article
    if edit.is_anon and is_main_article and edit.byte_delta >= 5000 and not comment:
        score += 0.55
        reasons.append(
            f"Sudden +{edit.byte_delta:,} byte insertion by anonymous editor with no summary"
        )

    # Extra risk weight for anonymous/temporary accounts on top of existing cues
    if reasons and edit.is_anon:
        score = min(0.96, score + 0.1)

    if score >= 0.78:
        return (
            VandalismAssessment(
                flagged=True,
                severity="high",
                confidence=round(min(0.97, score), 2),
                reason="; ".join(reasons),
                source="heuristic",
            ),
            True,
        )
    if score >= 0.52:
        return (
            VandalismAssessment(
                flagged=True,
                severity="medium",
                confidence=round(min(0.85, score), 2),
                reason="; ".join(reasons),
                source="heuristic",
            ),
            True,
        )

    return VandalismAssessment(), False


class SlidingRateLimiter:
    """Simple per-minute sliding-window rate limiter to protect LLM API budgets."""

    def __init__(self, max_calls_per_minute: int) -> None:
        self.max_calls_per_minute = max(1, max_calls_per_minute)
        self._timestamps: deque[float] = deque()

    def allow(self, now: Optional[float] = None) -> bool:
        current = now if now is not None else time.time()
        cutoff = current - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_calls_per_minute:
            return False
        self._timestamps.append(current)
        return True


class AIService:
    """Coordinates LLM calls (OpenAI / Anthropic / Gemini) and deterministic fallbacks."""

    def __init__(
        self,
        settings: Settings,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.settings = settings
        self._rate_limiter = SlidingRateLimiter(settings.llm_max_calls_per_minute)
        self._pulse_cache: dict[str, PulseSummary] = {}
        self._external_client = http_client

    @property
    def provider_label(self) -> str:
        return self.settings.provider_label()

    @property
    def llm_enabled(self) -> bool:
        return self.settings.resolved_llm_provider() != "heuristic"

    def clear_cache(self) -> None:
        """Clear cached Pulse summaries (e.g. when switching stream modes)."""
        self._pulse_cache.clear()

    async def _call_llm(self, system_prompt: str, user_prompt: str, max_tokens: int = 240) -> Optional[str]:
        """Dispatch a chat completion to the configured LLM provider within rate limits."""
        provider = self.settings.resolved_llm_provider()
        if provider == "heuristic":
            return None
        if not self._rate_limiter.allow():
            logger.info("LLM rate limit reached (%d/min); using heuristic fallback", self.settings.llm_max_calls_per_minute)
            return None

        owns_client = self._external_client is None
        client = self._external_client or httpx.AsyncClient(timeout=httpx.Timeout(12.0))
        try:
            if provider == "openai":
                api_key = self.settings.effective_api_key("openai")
                resp = await client.post(
                    f"{self.settings.openai_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.openai_model,
                        "temperature": 0.2,
                        "max_tokens": max_tokens,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]

            if provider == "anthropic":
                api_key = self.settings.effective_api_key("anthropic")
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.anthropic_model,
                        "max_tokens": max_tokens,
                        "temperature": 0.2,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": user_prompt}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                blocks = data.get("content", [])
                return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

            if provider == "gemini":
                api_key = self.settings.effective_api_key("gemini")
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{self.settings.gemini_model}:generateContent"
                )
                resp = await client.post(
                    url,
                    params={"key": api_key},
                    headers={"Content-Type": "application/json"},
                    json={
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    return "".join(p.get("text", "") for p in parts)
                return None

            return None
        except Exception as exc:
            logger.warning("LLM call failed (%s): %s", provider, exc)
            return None
        finally:
            if owns_client:
                await client.aclose()

    async def verify_vandalism_with_llm(
        self, edit: WikiEdit, initial: VandalismAssessment
    ) -> VandalismAssessment:
        """Refine a suspicious edit's vandalism assessment using the configured LLM."""
        if not self.llm_enabled:
            return initial

        system_prompt = (
            "You are a Wikipedia anti-vandalism analyst for a live media-monitoring dashboard. "
            "Given metadata about a single edit, decide whether it looks like vandalism. "
            "Respond ONLY with valid JSON: "
            '{"is_vandalism": true|false, "severity": "none"|"low"|"medium"|"high", '
            '"confidence": 0.0-1.0, "reason": "one concise sentence <= 18 words"}.'
        )
        user_prompt = json.dumps(
            {
                "wiki": edit.wiki,
                "title": edit.title,
                "user": edit.user,
                "is_anon": edit.is_anon,
                "is_bot": edit.is_bot,
                "namespace": edit.namespace,
                "old_length": edit.old_length,
                "new_length": edit.new_length,
                "byte_delta": edit.byte_delta,
                "comment": edit.comment[:240],
                "heuristic_signal": initial.reason,
            },
            ensure_ascii=False,
        )

        raw = await self._call_llm(system_prompt, user_prompt, max_tokens=140)
        if not raw:
            return initial

        parsed = _extract_json_object(raw)
        if not parsed:
            return initial

        flagged = bool(parsed.get("is_vandalism", initial.flagged))
        severity = str(parsed.get("severity", initial.severity if flagged else "none")).lower()
        if severity not in ("none", "low", "medium", "high"):
            severity = "medium" if flagged else "none"
        if not flagged:
            severity = "none"

        try:
            confidence = float(parsed.get("confidence", initial.confidence))
            confidence = max(0.0, min(1.0, round(confidence, 2)))
        except (TypeError, ValueError):
            confidence = initial.confidence

        reason = str(parsed.get("reason") or initial.reason).strip()[:180]
        return VandalismAssessment(
            flagged=flagged,
            severity=severity,  # type: ignore[arg-type]
            confidence=confidence,
            reason=reason if flagged else "",
            source="llm",
        )

    def _build_heuristic_pulse(
        self, stats: DashboardStats, recent_edits: list[WikiEdit], now: float
    ) -> PulseSummary:
        """Build a concise, data-grounded plain-English briefing without calling an external LLM."""
        scope = stats.wiki_filter or "all Wikimedia projects"
        if stats.edits_last_5m == 0:
            return PulseSummary(
                wiki_filter=stats.wiki_filter,
                summary=f"Listening for live edits across {scope}. Trends will appear here as activity streams in.",
                highlights=[
                    "Waiting for incoming edits in the current 5-minute window.",
                    "Use the wiki selector above to focus on a single edition (e.g. enwiki).",
                ],
                provider="heuristic",
                generated_at=now,
                edit_sample_size=0,
            )

        top = stats.top_pages
        lead_page = top[0] if top else None
        human_pct = round((stats.human_edits_last_5m / max(1, stats.edits_last_5m)) * 100)

        parts: list[str] = [
            f"Activity across {scope} is running at {stats.edits_per_minute} edits/min "
            f"({stats.edits_last_5m} edits across {stats.active_pages_5m} pages in the last 5 minutes, {human_pct}% human)."
        ]

        if lead_page:
            runner_up = top[1] if len(top) > 1 else None
            if runner_up and runner_up.edit_count >= 2:
                parts.append(
                    f'Top momentum is centered on "{lead_page.title}" ({lead_page.edit_count} edits by '
                    f"{lead_page.unique_editors} editor{'s' if lead_page.unique_editors != 1 else ''} on {lead_page.wiki}), "
                    f'followed by "{runner_up.title}" ({runner_up.edit_count} edits).'
                )
            else:
                parts.append(
                    f'"{lead_page.title}" leads recent edits on {lead_page.wiki} with '
                    f"{lead_page.edit_count} edit{'s' if lead_page.edit_count != 1 else ''} "
                    f"({lead_page.net_byte_delta:+,} net bytes)."
                )

        if stats.flagged_last_5m > 0:
            flagged_titles = []
            for ev in recent_edits:
                if ev.vandalism.flagged and ev.title not in flagged_titles:
                    flagged_titles.append(ev.title)
                if len(flagged_titles) >= 2:
                    break
            target_str = ", ".join(f'"{t}"' for t in flagged_titles) if flagged_titles else "active pages"
            parts.append(
                f"Vandalism watch: {stats.flagged_last_5m} suspicious or rollback "
                f"event{'s' if stats.flagged_last_5m != 1 else ''} flagged recently (notably {target_str})."
            )
        else:
            parts.append("No high-risk vandalism patterns have been flagged in the current 5-minute window.")

        highlights: list[str] = []
        if top:
            top_names = ", ".join(f"{p.title} ({p.edit_count})" for p in top[:3])
            highlights.append(f"Hottest pages (5m): {top_names}")
        if not stats.wiki_filter and stats.available_wikis:
            top_wikis = ", ".join(f"{w.wiki} ({w.count})" for w in stats.available_wikis[:3])
            highlights.append(f"Most active wikis: {top_wikis}")
        else:
            highlights.append(
                f"Editor mix: {stats.human_edits_last_5m} human vs {stats.bot_edits_last_5m} bot edits"
            )
        if stats.flagged_last_5m > 0:
            highlights.append(f"Vandalism alerts: {stats.flagged_last_5m} flagged in last 5 minutes")
        else:
            highlights.append("Vandalism alerts: Clean — 0 edits flagged in last 5 minutes")

        return PulseSummary(
            wiki_filter=stats.wiki_filter,
            summary=" ".join(parts),
            highlights=highlights,
            provider="heuristic",
            generated_at=now,
            edit_sample_size=stats.edits_last_5m,
        )

    async def generate_pulse_summary(
        self,
        stats: DashboardStats,
        recent_edits: list[WikiEdit],
        *,
        force: bool = False,
        now: Optional[float] = None,
    ) -> PulseSummary:
        """Return a cached or freshly generated Pulse summary for the given wiki filter."""
        current = now if now is not None else time.time()
        cache_key = stats.wiki_filter or "__all__"
        cached = self._pulse_cache.get(cache_key)

        # Never cache the 0-edit waiting placeholder so the first real edits update immediately
        if stats.edits_last_5m == 0:
            return self._build_heuristic_pulse(stats, recent_edits, current)

        if cached is not None and cached.edit_sample_size > 0:
            age = current - cached.generated_at
            min_ttl = 4.0 if force else float(self.settings.pulse_refresh_min_seconds)
            # In heuristic mode without force, still refresh if sample size grew meaningfully
            if age < min_ttl and (
                self.llm_enabled or abs(stats.edits_last_5m - cached.edit_sample_size) < 10
            ):
                return cached

        # If no LLM key is configured, return heuristic briefing immediately
        if not self.llm_enabled:
            summary = self._build_heuristic_pulse(stats, recent_edits, current)
            self._pulse_cache[cache_key] = summary
            return summary

        system_prompt = (
            "You are an editorial analyst for WikiPulse, a live media-monitoring dashboard watching Wikipedia edits. "
            "Write a crisp, plain-English Pulse Summary of what is trending right now based ONLY on the provided data. "
            "Do not invent facts not present in the edits. "
            "Respond ONLY with valid JSON: "
            '{"summary": "2-3 concise sentences in plain English", '
            '"highlights": ["short bullet 1", "short bullet 2", "short bullet 3"]}.'
        )
        flagged_samples = [
            {
                "title": e.title,
                "wiki": e.wiki,
                "delta": e.byte_delta,
                "reason": e.vandalism.reason,
            }
            for e in recent_edits
            if e.vandalism.flagged
        ][:5]

        user_prompt = json.dumps(
            {
                "scope": stats.wiki_filter or "all Wikimedia wikis",
                "edits_per_minute": stats.edits_per_minute,
                "edits_last_5m": stats.edits_last_5m,
                "human_edits_5m": stats.human_edits_last_5m,
                "bot_edits_5m": stats.bot_edits_last_5m,
                "flagged_last_5m": stats.flagged_last_5m,
                "top_pages_5m": [
                    {
                        "title": p.title,
                        "wiki": p.wiki,
                        "edits": p.edit_count,
                        "editors": p.unique_editors,
                        "net_bytes": p.net_byte_delta,
                        "flagged": p.flagged_count,
                    }
                    for p in stats.top_pages[:10]
                ],
                "top_wikis": [{"wiki": w.wiki, "count": w.count} for w in stats.available_wikis[:5]],
                "recent_flagged_edits": flagged_samples,
            },
            ensure_ascii=False,
        )

        raw = await self._call_llm(system_prompt, user_prompt, max_tokens=260)
        parsed = _extract_json_object(raw) if raw else None

        if parsed and parsed.get("summary"):
            raw_highlights = parsed.get("highlights")
            highlights = (
                [str(item).strip() for item in raw_highlights if str(item).strip()][:4]
                if isinstance(raw_highlights, list)
                else []
            )
            result = PulseSummary(
                wiki_filter=stats.wiki_filter,
                summary=str(parsed["summary"]).strip(),
                highlights=highlights,
                provider=self.provider_label,
                generated_at=current,
                edit_sample_size=stats.edits_last_5m,
            )
        else:
            result = self._build_heuristic_pulse(stats, recent_edits, current)

        self._pulse_cache[cache_key] = result
        return result
