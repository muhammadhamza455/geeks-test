"""Unit tests for vandalism detection heuristics, LLM JSON parsing, and Pulse summaries."""

import json
import httpx
import pytest

from app.ai_service import (
    AIService,
    SlidingRateLimiter,
    _extract_json_object,
    heuristic_vandalism_check,
)
from app.config import Settings
from app.models import DashboardStats, TopPageStat, VandalismAssessment, WikiCount, WikiEdit


def _edit(**overrides) -> WikiEdit:
    base = dict(
        id="e1",
        wiki="enwiki",
        title="2026 FIFA World Cup",
        title_url="https://en.wikipedia.org/wiki/2026_FIFA_World_Cup",
        user="NormalUser",
        is_bot=False,
        is_anon=False,
        is_minor=False,
        namespace=0,
        comment="Added citation",
        old_length=50000,
        new_length=50120,
        byte_delta=120,
        timestamp=1700000000.0,
    )
    base.update(overrides)
    return WikiEdit(**base)


def test_heuristic_flags_severe_blanking():
    edit = _edit(
        user="198.51.100.42",
        is_anon=True,
        comment="",
        old_length=80000,
        new_length=40,
        byte_delta=-79960,
    )
    assessment, verify = heuristic_vandalism_check(edit)
    assert assessment.flagged is True
    assert assessment.severity == "high"
    assert "blanking" in assessment.reason.lower()
    assert verify is True


def test_heuristic_flags_profanity_or_prank_summary():
    edit = _edit(
        user="~2026-441920",
        is_anon=True,
        comment="poop lololol test hack",
        old_length=12000,
        new_length=11200,
        byte_delta=-800,
    )
    assessment, verify = heuristic_vandalism_check(edit)
    assert assessment.flagged is True
    assert assessment.severity in ("medium", "high")
    assert verify is True


def test_heuristic_recognizes_vandalism_rollback():
    edit = _edit(
        user="ClueBot NG",
        is_bot=True,
        comment="Reverted vandalism by 198.51.100.42 (rvv)",
        old_length=100,
        new_length=80000,
        byte_delta=79900,
    )
    assessment, verify = heuristic_vandalism_check(edit)
    assert assessment.flagged is True
    assert assessment.severity == "low"
    assert "rollback" in assessment.reason.lower()
    assert verify is False


def test_heuristic_ignores_normal_edits_and_benign_bots():
    normal = _edit(comment="Copyedit grammar in lead section", byte_delta=-45)
    a1, v1 = heuristic_vandalism_check(normal)
    assert a1.flagged is False
    assert v1 is False

    benign_bot = _edit(
        user="InternetArchiveBot",
        is_bot=True,
        comment="Rescuing 2 sources",
        byte_delta=-2000,
    )
    a2, v2 = heuristic_vandalism_check(benign_bot)
    assert a2.flagged is False
    assert v2 is False

    redirect_edit = _edit(
        comment="Redirect to main article after merge",
        old_length=4000,
        new_length=60,
        byte_delta=-3940,
    )
    a3, _ = heuristic_vandalism_check(redirect_edit)
    assert a3.flagged is False


def test_extract_json_object_handles_fences_and_noise():
    assert _extract_json_object('{"a": 1}') == {"a": 1}
    assert _extract_json_object('```json\n{"a": 2}\n```') == {"a": 2}
    assert _extract_json_object('Sure! Here is JSON: {"ok": true} thanks') == {"ok": True}
    assert _extract_json_object("not json at all") is None


def test_rate_limiter_enforces_budget():
    limiter = SlidingRateLimiter(max_calls_per_minute=2)
    assert limiter.allow(now=100.0) is True
    assert limiter.allow(now=110.0) is True
    assert limiter.allow(now=120.0) is False
    # After 60s window expires, slot frees up
    assert limiter.allow(now=161.0) is True


@pytest.mark.asyncio
async def test_pulse_summary_heuristic_and_caching():
    settings = Settings(llm_provider="heuristic", openai_api_key="", anthropic_api_key="", gemini_api_key="")
    service = AIService(settings)

    stats = DashboardStats(
        edits_per_minute=42,
        edits_last_5m=180,
        flagged_last_5m=2,
        human_edits_last_5m=130,
        bot_edits_last_5m=50,
        total_processed=180,
        active_pages_5m=95,
        top_pages=[
            TopPageStat(
                title="2026 FIFA World Cup",
                wiki="enwiki",
                title_url="https://en.wikipedia.org/wiki/2026_FIFA_World_Cup",
                edit_count=12,
                unique_editors=6,
                net_byte_delta=450,
                flagged_count=1,
                last_edit_ts=1700000000.0,
            )
        ],
        available_wikis=[WikiCount(wiki="enwiki", count=100), WikiCount(wiki="frwiki", count=80)],
        updated_at=1700000000.0,
    )
    flagged_edit = _edit(
        vandalism=VandalismAssessment(flagged=True, severity="high", confidence=0.9, reason="Blanking")
    )

    p1 = await service.generate_pulse_summary(stats, [flagged_edit], now=1000.0)
    assert p1.provider == "heuristic"
    assert "42 edits/min" in p1.summary
    assert "2026 FIFA World Cup" in p1.summary
    assert len(p1.highlights) >= 2

    # Cached within TTL
    p2 = await service.generate_pulse_summary(stats, [flagged_edit], now=1005.0)
    assert p2.generated_at == p1.generated_at


@pytest.mark.asyncio
async def test_zero_edit_placeholder_pulse_is_not_cached():
    settings = Settings(llm_provider="heuristic")
    service = AIService(settings)

    empty_stats = DashboardStats(updated_at=1000.0)
    p_empty = await service.generate_pulse_summary(empty_stats, [], now=1000.0)
    assert "Listening for live edits" in p_empty.summary

    active_stats = DashboardStats(
        edits_per_minute=5,
        edits_last_5m=5,
        human_edits_last_5m=5,
        active_pages_5m=2,
        updated_at=1001.0,
    )
    # 1 second later, real edits arrive -> must NOT return cached empty placeholder
    p_active = await service.generate_pulse_summary(active_stats, [], force=True, now=1001.0)
    assert "5 edits/min" in p_active.summary


@pytest.mark.asyncio
async def test_llm_vandalism_and_pulse_with_mocked_openai():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        system_msg = body["messages"][0]["content"]
        if "anti-vandalism" in system_msg:
            content = json.dumps(
                {
                    "is_vandalism": True,
                    "severity": "high",
                    "confidence": 0.94,
                    "reason": "Anonymous editor blanked nearly the entire article without explanation.",
                }
            )
        else:
            content = json.dumps(
                {
                    "summary": "English Wikipedia is seeing heightened activity around the 2026 FIFA World Cup.",
                    "highlights": ["2026 FIFA World Cup leads with 12 edits", "2 vandalism flags logged"],
                }
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        settings = Settings(
            llm_provider="openai",
            openai_api_key="sk-test-key",
            openai_model="gpt-4o-mini",
        )
        service = AIService(settings, http_client=client)
        assert service.llm_enabled is True

        edit = _edit(is_anon=True, byte_delta=-50000, old_length=50100, new_length=100, comment="")
        initial, _ = heuristic_vandalism_check(edit)
        refined = await service.verify_vandalism_with_llm(edit, initial)
        assert refined.source == "llm"
        assert refined.flagged is True
        assert refined.confidence == 0.94

        stats = DashboardStats(
            edits_per_minute=20,
            edits_last_5m=60,
            flagged_last_5m=1,
            human_edits_last_5m=50,
            bot_edits_last_5m=10,
            active_pages_5m=15,
            top_pages=[
                TopPageStat(
                    title="2026 FIFA World Cup",
                    wiki="enwiki",
                    title_url="https://en.wikipedia.org/wiki/2026_FIFA_World_Cup",
                    edit_count=12,
                    unique_editors=5,
                    net_byte_delta=200,
                    flagged_count=1,
                    last_edit_ts=1700000000.0,
                )
            ],
            updated_at=1700000000.0,
        )
        pulse = await service.generate_pulse_summary(stats, [edit], force=True, now=2000.0)
        assert pulse.provider == "openai:gpt-4o-mini"
        assert "2026 FIFA World Cup" in pulse.summary
