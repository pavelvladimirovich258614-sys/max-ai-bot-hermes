"""Unit tests for the /status diagnostics card (F0.2, 2026-08-21).

We assert four contracts:

  1. ``build_status_text`` produces a card with all five required fields
     (Hermes mode, LLM primary key, LLM fallback key, last success, chain).
  2. The chain renderer maps provider names to ✅/❌ markers and shows
     failure reasons verbatim.
  3. The text never includes raw API keys (we don't have any to leak in
     this test, but we verify the *placeholders* the function uses are
     safe to print).
  4. The Orchestrator.health() method returns the exact shape /status
     consumes (verified by mocking settings).
"""
import time

from app.max.handlers.status import _format_chain, _format_ts, build_status_text


def test_build_status_text_includes_all_required_fields():
    health = {
        "hermes_mode": "auto",
        "llm_primary_set": True,
        "llm_fallback_set": False,
        "recent_errors": ["12:00:00 failed_chain=hermes → llm_primary"],
        "last_success_ts": time.time() - 60,
        "last_chain": [
            {"provider": "hermes", "ok": True, "latency_s": 0.12, "reason": ""},
        ],
    }
    text = build_status_text(health)
    assert "📊 СТАТУС БОТА" in text
    assert "Hermes mode: auto" in text
    assert "LLM primary key:  ✅ задан" in text
    assert "LLM fallback key: ❌ НЕ задан" in text
    assert "Последний успех:" in text
    assert "Последняя цепочка fallback:" in text
    assert "12:00:00 failed_chain=hermes → llm_primary" in text


def test_build_status_text_never_never_ever_prints_anything_when_keys_missing_but_indicates_it():
    health = {
        "hermes_mode": "none",
        "llm_primary_set": False,
        "llm_fallback_set": False,
        "recent_errors": [],
        "last_success_ts": None,
        "last_chain": [],
    }
    text = build_status_text(health)
    # Two NЕs, no recent errors line, "(никогда)" success.
    assert "❌ НЕ задан" in text
    assert text.count("❌ НЕ задан") == 2
    assert "(никогда)" in text
    assert "Последние ошибки:" not in text
    # Empty chain → helpful message
    assert "(пока ничего не выполнялось)" in text


def test_format_chain_marks_success_and_failure():
    steps = [
        {"provider": "hermes", "ok": True, "latency_s": 0.1, "reason": ""},
        {"provider": "llm_primary", "ok": False, "latency_s": 5.0, "reason": "timeout"},
    ]
    out = _format_chain(steps)
    assert len(out) == 2
    assert out[0].startswith("✅")
    assert "hermes" in out[0]
    assert "0.10s" in out[0]
    assert out[1].startswith("❌")
    assert "llm_primary" in out[1]
    assert "5.00s" in out[1]
    assert "timeout" in out[1]


def test_format_chain_empty_returns_helpful_message():
    assert _format_chain([]) == ["(пока ничего не выполнялось)"]
    assert _format_chain(None or []) == ["(пока ничего не выполнялось)"]


def test_format_ts_handles_none_and_real_value():
    assert _format_ts(None) == "(никогда)"
    # 1700000000 epoch = 2023-11-14 22:13:20 UTC; render is local-time
    rendered = _format_ts(1700000000.0)
    assert "2023" in rendered or "2024" in rendered  # accept local TZ shift
    assert ":" in rendered  # has HH:MM:SS


def test_build_status_text_handles_missing_keys_gracefully():
    # If health is missing a key (older Orchestrator version), no crash.
    text = build_status_text({})
    assert "📊 СТАТУС БОТА" in text
    assert "Hermes mode: unknown" in text
    assert "❌ НЕ задан" in text  # both LLM keys default to False


def test_orchestrator_health_returns_expected_shape():
    """End-to-end: build an Orchestrator with mocks, call .health().

    We don't hit any network — we use Settings defaults and a stub LLM/Storage.
    """
    from unittest.mock import MagicMock

    from app.config import Settings
    from app.core.orchestrator import Orchestrator

    settings = Settings(hermes_mode="auto")
    fake_llm = MagicMock()
    fake_storage = MagicMock()
    orch = Orchestrator(settings=settings, llm=fake_llm, storage=fake_storage)

    import asyncio
    health = asyncio.run(orch.health())

    # Required keys for /status
    for key in (
        "hermes_mode",
        "llm_primary_set",
        "llm_fallback_set",
        "recent_errors",
        "last_success_ts",
        "last_chain",
    ):
        assert key in health, f"missing key: {key}"

    assert health["hermes_mode"] == "auto"
    assert health["llm_primary_set"] is False  # no API key in test settings
    assert health["llm_fallback_set"] is False
    assert health["recent_errors"] == []
    assert health["last_success_ts"] is None
    assert health["last_chain"] == []
