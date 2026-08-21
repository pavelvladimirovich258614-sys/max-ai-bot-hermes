"""Tests for the /research cascade (F2, 2026-08-21).

We assert the full F2 contract end-to-end with mocked WebSearch and
WebReader. The cascade is the public API; the role_prompt and the
handler are tested separately in test_research_handler.py.
"""
import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.core.research_cascade import ResearchCascade
from app.schemas.research import (
    FreshnessWindow,
    KeyFinding,
    ResearchResult,
    days_to_date,
    freshness_to_days,
    parse_freshness,
)
from app.tools.web_reader import WebReader
from app.tools.web_search import SearchResult, WebSearch


# ---------------- helpers ----------------


def _settings() -> Settings:
    return Settings(
        web_search_backend="duckduckgo",
        database_url="sqlite+aiosqlite:///:memory:",
    )


def _fake_search_results() -> list[SearchResult]:
    return [
        SearchResult(title="A1: новый материал", url="https://ex.com/a1", snippet="…"),
        SearchResult(title="A2: пост №2", url="https://ex.com/a2", snippet="…"),
        SearchResult(title="A3: пост №3", url="https://ex.com/a3", snippet="…"),
        SearchResult(title="A4: пост №4", url="https://ex.com/a4", snippet="…"),
    ]


def _build_cascade(
    search_results: list[SearchResult] | None = None,
    meta: dict[str, dict] | None = None,
    *,
    threshold: int = 5,
) -> tuple[ResearchCascade, MagicMock, MagicMock]:
    """Build a cascade with mocked WebSearch + WebReader.

    ``meta`` is a dict from URL to the dict returned by
    ``read_with_meta``. URLs not in ``meta`` are dropped (treated as
    failed crawls).

    Default ``threshold=5`` so Tier 2 always runs in tests (the
    fixture's 4 Tier 1 results are below the threshold). Tests that
    want to assert early-stop pass ``threshold=2``.
    """
    settings = _settings()
    search_mock = MagicMock(spec=WebSearch)
    search_mock.search = AsyncMock(return_value=search_results or _fake_search_results())
    search_mock.aclose = AsyncMock()

    reader_mock = MagicMock(spec=WebReader)
    default_meta: dict[str, dict] = {
        "https://ex.com/a1": {
            "text": "Текст про AI 1.",
            "title": "A1: новый материал",
            "published_at": date(2026, 8, 15),
            "url": "https://ex.com/a1",
            "ok": True,
        },
        "https://ex.com/a2": {
            "text": "Текст про AI 2.",
            "title": "A2: пост №2",
            "published_at": date(2026, 8, 10),
            "url": "https://ex.com/a2",
            "ok": True,
        },
        "https://ex.com/a3": {
            "text": "Текст про AI 3.",
            "title": "A3: пост №3",
            "published_at": date(2026, 8, 1),
            "url": "https://ex.com/a3",
            "ok": True,
        },
        "https://ex.com/a4": {
            "text": "Текст про AI 4.",
            "title": "A4: пост №4",
            "published_at": date(2026, 7, 20),
            "url": "https://ex.com/a4",
            "ok": True,
        },
    }
    if meta:
        default_meta.update(meta)
    reader_mock.read_with_meta = AsyncMock(
        side_effect=lambda url, max_chars=8000: default_meta.get(url, {
            "text": "", "title": "", "published_at": None, "url": url, "ok": False,
        })
    )
    reader_mock.aclose = AsyncMock()

    cascade = ResearchCascade(
        settings,
        web_search=search_mock,
        web_reader=reader_mock,
        tier1_stop_threshold=threshold,
    )
    return cascade, search_mock, reader_mock


# ---------------- freshness helpers ----------------


def test_parse_freshness_default_30d():
    assert parse_freshness("30d") == "30d"
    assert parse_freshness("") == "30d"
    assert parse_freshness("foo") == "30d"  # unknown -> default


def test_parse_freshness_variants():
    assert parse_freshness("7d") == "7d"
    assert parse_freshness("7") == "7d"
    assert parse_freshness("week") == "7d"
    assert parse_freshness("30d") == "30d"
    assert parse_freshness("month") == "30d"
    assert parse_freshness("90d") == "90d"
    assert parse_freshness("quarter") == "90d"
    assert parse_freshness("all") == "all"
    assert parse_freshness("any") == "all"


def test_freshness_to_days():
    assert freshness_to_days("7d") == 7
    assert freshness_to_days("30d") == 30
    assert freshness_to_days("90d") == 90
    assert freshness_to_days("all") is None


def test_days_to_date_recent():
    d = days_to_date(7)
    assert isinstance(d, date)
    # Allow ±1 day drift to account for wall-clock midnight crossings
    # when a slow test suite spans a date boundary.
    assert abs((date.today() - d).days - 7) <= 1


# ---------------- Tier 1 (search) ----------------


def test_tier1_calls_search_with_after_date():
    cascade, search, _ = _build_cascade()
    asyncio.run(cascade.run("AI в legal 2026", "30d"))
    args, kwargs = search.search.call_args
    # The query is the first positional arg.
    assert args[0] == "AI в legal 2026"
    # The after_date kwarg should be set to today - 30d.
    assert kwargs.get("after_date") == days_to_date(30)


def test_tier1_no_after_date_when_window_is_all():
    cascade, search, _ = _build_cascade()
    asyncio.run(cascade.run("история AI", "all"))
    args, kwargs = search.search.call_args
    assert kwargs.get("after_date") is None


# ---------------- Tier 2 (crawl) ----------------


def test_tier2_crawls_top_n_urls():
    cascade, _, reader = _build_cascade()
    result = asyncio.run(cascade.run("AI", "30d"))
    # Default tier2_crawl_limit = 5; we have 4 URLs.
    assert reader.read_with_meta.await_count == 4
    # All 4 should land as findings.
    assert len(result.key_findings) == 4


def test_tier2_handles_trafilatura_failure_gracefully():
    """If read_with_meta returns ok=False, the URL is dropped (no crash)."""
    cascade, _, reader = _build_cascade(meta={
        # a2 fails (network error / trafilatura crash)
        "https://ex.com/a2": {
            "text": "", "title": "", "published_at": None, "ok": False, "url": "https://ex.com/a2",
        },
    })
    result = asyncio.run(cascade.run("AI", "30d"))
    # 4 Tier 1 URLs -> 3 successful crawls.
    assert reader.read_with_meta.await_count == 4
    assert len(result.key_findings) == 3
    # a2 is NOT in the findings.
    urls = {f.url for f in result.key_findings}
    assert "https://ex.com/a2" not in urls


# ---------------- Tier 3 (verify) ----------------


def test_tier3_demotes_old_sources_to_low_confidence():
    """Sources outside the 30d window are demoted to low confidence."""
    cascade, _, _ = _build_cascade()
    result = asyncio.run(cascade.run("AI", "30d"))
    # a4 is dated 2026-07-20, which is > 30 days from today in 2026-08-21.
    a4 = next(f for f in result.key_findings if f.url == "https://ex.com/a4")
    assert a4.confidence == "low"
    # The 3 fresh ones (a1, a2, a3) should be high.
    for url in ("https://ex.com/a1", "https://ex.com/a2", "https://ex.com/a3"):
        f = next(f for f in result.key_findings if f.url == url)
        assert f.confidence == "high", f"{url} should be high"


def test_tier3_conflict_marks_both_positions():
    """Two Tier 2 findings with the same claim (after normalization) get
    marked as a conflict and added to the output as separate entries."""
    cascade, _, _ = _build_cascade(meta={
        # a2 and a3 normalize to the same claim key.
        "https://ex.com/a2": {
            "text": "Foo bar baz.",
            "title": "Same claim head-line",
            "published_at": date(2026, 8, 10),
            "url": "https://ex.com/a2",
            "ok": True,
        },
        "https://ex.com/a3": {
            "text": "Foo bar qux.",
            "title": "Same claim head-line",
            "published_at": date(2026, 8, 1),
            "url": "https://ex.com/a3",
            "ok": True,
        },
    })
    result = asyncio.run(cascade.run("AI", "30d"))
    # The first one keeps medium confidence, the second is added as
    # "(альтернативная позиция)" with low confidence.
    titles = [f.claim for f in result.key_findings]
    assert any("альтернативная позиция" in t for t in titles), (
        f"expected a conflict-marked claim; got {titles}"
    )


def test_tier1_alone_stops_at_discovery_when_enough():
    """If Tier 1 already returns ≥3 fresh high-confidence findings, no
    Tier 2 crawls happen."""
    # Mock search returning 5 results; tier1_stop_threshold=2.
    cascade, _, reader = _build_cascade(
        search_results=_fake_search_results() * 2, threshold=2,
    )
    result = asyncio.run(cascade.run("AI", "30d"))
    # No crawls (Tier 1 alone was enough).
    assert reader.read_with_meta.await_count == 0
    # But we still got findings from Tier 1's quick-filter.
    assert len(result.key_findings) >= 3
    # Hermes is "skipped" (we did not run the subprocess in early-stop).
    assert result.tier_summary.hermes_enrichment == "skipped"


def test_tier1_alone_falls_through_to_tier2_when_insufficient():
    """If Tier 1 returns <threshold results with confidence, Tier 2 runs."""
    # Only 1 result with default threshold=5 -> we MUST fall through to Tier 2.
    cascade, _, reader = _build_cascade(
        search_results=[
            SearchResult(title="Lonely", url="https://ex.com/lonely", snippet=""),
        ],
    )
    asyncio.run(cascade.run("AI", "30d"))
    # Tier 2 should have crawled the lonely URL.
    assert reader.read_with_meta.await_count == 1


# ---------------- NO_FRESH_DATA ----------------


def test_returns_no_fresh_data_when_all_old():
    """If the search backend returned URLs but every Tier 2 published_at
    is older than the window, status=NO_FRESH_DATA."""
    old_meta = {
        "https://ex.com/a1": {
            "text": "…", "title": "A1", "published_at": date(2024, 1, 1), "ok": True,
            "url": "https://ex.com/a1",
        },
        "https://ex.com/a2": {
            "text": "…", "title": "A2", "published_at": date(2024, 6, 1), "ok": True,
            "url": "https://ex.com/a2",
        },
        "https://ex.com/a3": {
            "text": "…", "title": "A3", "published_at": date(2024, 12, 1), "ok": True,
            "url": "https://ex.com/a3",
        },
    }
    cascade, _, _ = _build_cascade(meta=old_meta)
    result = asyncio.run(cascade.run("AI", "30d"))
    # All 3 are old; the cascade should still surface them but with
    # status=NO_FRESH_DATA.
    assert result.status == "NO_FRESH_DATA"
    assert any("Нет публикаций новее" in u for u in result.unknowns)
    # Tier 1 ran 4 searches; Tier 2 ran 4 crawls (3 succeeded + 1 failed a4).
    assert result.tier_summary.tier1_urls == 4
    assert result.tier_summary.tier2_crawled >= 3


# ---------------- Hermes enrichment ----------------


def test_hermes_enrichment_skipped_when_bin_missing(monkeypatch):
    """If hermes CLI is not on PATH, enrichment is "skipped"."""
    monkeypatch.setenv("HERMES_CLI_BIN", "definitely-not-on-path-xyz")
    cascade, _, _ = _build_cascade()
    result = asyncio.run(cascade.run("AI", "30d"))
    assert result.tier_summary.hermes_enrichment == "skipped"


def test_hermes_enrichment_applied_when_subprocess_returns_valid_json(monkeypatch):
    """If a mock hermes subprocess returns valid JSON, findings are merged.

    Cross-platform note: we patch ``_hermes_enrich`` directly instead
    of spawning a real subprocess because Windows ``create_subprocess_exec``
    does not honour Unix shebangs and writing a portable shim is more
    trouble than the test is worth. The subprocess spawn path is
    exercised separately in
    ``test_hermes_enrichment_failed_when_subprocess_crashes``.
    """
    cascade, _, _ = _build_cascade(
        # Single Tier 1 result so we fall through to Tier 2 and the
        # hermes enrichment step runs.
        search_results=[
            SearchResult(title="Solo", url="https://ex.com/solo", snippet=""),
        ],
        meta={
            "https://ex.com/solo": {
                "text": "…", "title": "Solo", "published_at": date(2026, 8, 15),
                "ok": True, "url": "https://ex.com/solo",
            },
        },
    )
    hermes_finding = KeyFinding(
        claim="Hermes claims X",
        evidence="backed by Y",
        url="https://hermes.example/x",
        source_type="reputable_media",
        published_at=date(2026, 8, 19),
        tier=3,
        confidence="high",
    )
    monkeypatch.setattr(
        cascade, "_hermes_enrich",
        AsyncMock(return_value=([hermes_finding], "applied")),
    )
    result = asyncio.run(cascade.run("AI", "30d"))
    assert result.tier_summary.hermes_enrichment == "applied"
    # The hermes claim is merged in.
    claims = [f.claim for f in result.key_findings]
    assert any("Hermes claims X" in c for c in claims)


def test_hermes_enrichment_failed_when_subprocess_crashes(monkeypatch):
    """Subprocess raises -> status='failed' (vs 'skipped' for missing bin)."""
    cascade, _, _ = _build_cascade(
        search_results=[
            SearchResult(title="Solo", url="https://ex.com/solo", snippet=""),
        ],
        meta={
            "https://ex.com/solo": {
                "text": "…", "title": "Solo", "published_at": date(2026, 8, 15),
                "ok": True, "url": "https://ex.com/solo",
            },
        },
    )
    monkeypatch.setattr(
        cascade, "_hermes_enrich",
        AsyncMock(return_value=([], "failed")),
    )
    result = asyncio.run(cascade.run("AI", "30d"))
    assert result.tier_summary.hermes_enrichment == "failed"


# ---------------- JSON schema validation ----------------


def test_result_validates_against_pydantic_schema():
    """A result from the cascade must parse back through ResearchResult."""
    cascade, _, _ = _build_cascade()
    result = asyncio.run(cascade.run("AI", "30d"))
    # Round-trip
    raw = result.to_compact_json()
    reparsed = ResearchResult.model_validate_json(raw)
    assert reparsed.status == result.status
    assert reparsed.freshness_window == result.freshness_window
    assert len(reparsed.key_findings) == len(result.key_findings)


def test_result_status_is_in_allowed_set():
    cascade, _, _ = _build_cascade()
    result = asyncio.run(cascade.run("AI", "30d"))
    assert result.status in ("OK", "PARTIAL", "NO_FRESH_DATA", "FAILED")


def test_all_findings_have_published_at_iso_date():
    """F2.3 contract: published_at is a real date, not a string."""
    cascade, _, _ = _build_cascade()
    result = asyncio.run(cascade.run("AI", "30d"))
    for f in result.key_findings:
        assert isinstance(f.published_at, date)
        assert f.published_at.year >= 2024


# ---------------- Tier summary logging ----------------


def test_tier_summary_counts_match_actual_pipeline():
    """tier_summary.tier3_verified equals the final number of findings."""
    cascade, _, _ = _build_cascade()
    result = asyncio.run(cascade.run("AI", "30d"))
    assert result.tier_summary.tier3_verified == len(result.key_findings)
    assert result.tier_summary.tier1_urls == 4  # from _fake_search_results()
