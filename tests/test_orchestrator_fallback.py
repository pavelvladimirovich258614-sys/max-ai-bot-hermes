"""Tests for the Orchestrator's three-level fallback chain (F0.1).

We assert four contracts:

  1. Hermes wins when it returns a non-empty answer.
  2. When Hermes returns None, the direct LLM is called; if it returns a
     string, that string is the result.
  3. When BOTH Hermes and LLM fail, the returned string:
       * contains "СЕРВИС ВРЕМЕННО НЕДОСТУПЕН"
       * lists the providers that were tried
       * hints at /status
       * never includes raw API keys / tokens
  4. The chain is recorded on the Orchestrator so /status can show it.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import Settings
from app.core.orchestrator import Orchestrator, ChainStep


def _make_orchestrator(hermes_answer, llm_side_effect):
    """Build an Orchestrator with Hermes + LLM mocked.

    ``hermes_answer``: what ``HermesClient.route()`` returns
                       (None for "unavailable").
    ``llm_side_effect``: what ``LLMClient.chat()`` does — either returns a
                        string (success) or raises an exception (failure).
    """
    settings = Settings(hermes_mode="auto")

    hermes = MagicMock()
    hermes.route = AsyncMock(return_value=hermes_answer)
    hermes.aclose = AsyncMock()

    llm = MagicMock()
    if isinstance(llm_side_effect, Exception):
        llm.chat = AsyncMock(side_effect=llm_side_effect)
    else:
        llm.chat = AsyncMock(return_value=llm_side_effect)
    llm.aclose = AsyncMock()

    storage = MagicMock()
    orch = Orchestrator(settings=settings, llm=llm, storage=storage)
    # Replace the real HermesClient with our mock.
    orch._hermes = hermes  # type: ignore[attr-defined]
    return orch, hermes, llm


# ---------------- 1) Hermes wins ----------------

def test_hermes_wins_when_it_returns_an_answer():
    orch, hermes, llm = _make_orchestrator(
        hermes_answer="Hermes says hi",
        llm_side_effect="LLM would also say hi",
    )
    out = asyncio.run(orch.run(role="chat", task="hello"))
    assert out == "Hermes says hi"
    hermes.route.assert_awaited_once()
    llm.chat.assert_not_awaited()  # we never hit the LLM
    chain = orch.last_chain()
    assert len(chain) == 1
    assert chain[0].provider == "hermes"
    assert chain[0].ok is True


# ---------------- 2) LLM fallback when Hermes is unavailable ----------------

def test_llm_used_when_hermes_unavailable():
    orch, hermes, llm = _make_orchestrator(
        hermes_answer=None,
        llm_side_effect="LLM fallback answer",
    )
    out = asyncio.run(orch.run(role="chat", task="hello"))
    assert out == "LLM fallback answer"
    hermes.route.assert_awaited_once()
    llm.chat.assert_awaited_once()
    chain = orch.last_chain()
    assert len(chain) == 2
    assert chain[0].provider == "hermes"
    assert chain[0].ok is False
    assert chain[1].provider == "llm_primary"
    assert chain[1].ok is True
    # /status timestamp is set on success
    assert orch.last_success_ts is not None


def test_llm_exception_path_returns_human_message():
    orch, hermes, llm = _make_orchestrator(
        hermes_answer=None,
        llm_side_effect=RuntimeError("all providers down"),
    )
    out = asyncio.run(orch.run(role="chat", task="hello"))
    # F0.1 contract: never silent, never empty.
    assert "СЕРВИС ВРЕМЕННО НЕДОСТУПЕН" in out
    assert "Попробовано:" in out
    # Both providers should be listed as tried.
    assert "hermes" in out
    assert "llm_primary" in out
    # /status hint
    assert "/status" in out
    # success timestamp NOT updated
    assert orch.last_success_ts is None
    # chain has all three providers marked as failed
    chain = orch.last_chain()
    assert len(chain) == 3
    failed_providers = [s.provider for s in chain if not s.ok]
    assert "hermes" in failed_providers
    assert "llm_primary" in failed_providers
    assert "llm_fallback" in failed_providers


# ---------------- 3) End-to-end: Hermes raises, LLM raises, all-fail msg ----------------

def test_full_chain_all_fail_returns_actionable_error():
    """The exact scenario from the F0.1 ticket."""
    orch, hermes, llm = _make_orchestrator(
        hermes_answer=None,
        llm_side_effect=ConnectionError("miniMax down, stepFun down"),
    )
    out = asyncio.run(orch.run(role="chat", task="hello"))

    # Exact contract per F0.1:
    #  - Says "service unavailable"
    #  - Lists tried providers
    #  - Hints at /status
    #  - Hints at LLM keys / Hermes RZA
    #  - Does NOT leak any key
    assert "СЕРВИС ВРЕМЕННО НЕДОСТУПЕН" in out
    assert "Попробовано:" in out
    assert "hermes" in out
    assert "llm_primary" in out
    assert "/status" in out
    # When no API keys are set (Settings defaults), the hint names them.
    assert "LLM_PRIMARY_API_KEY" in out or "LLM_FALLBACK_API_KEY" in out

    # The error must NOT contain anything that looks like a key
    for needle in ("Bearer ", "sk-", "eyJ", "x-api-key"):
        assert needle not in out, f"leak suspected: {needle!r} in {out!r}"

    # Errors are recorded for /status
    assert orch.recent_errors(), "expected at least one recent error"
    # And the chain is non-empty so /status can render it
    chain = orch.last_chain()
    assert chain, "expected non-empty chain after a failed run"
    # All steps in the chain are ChainStep instances
    for s in chain:
        assert isinstance(s, ChainStep)


# ---------------- 4) Hermes raises an exception ----------------

def test_hermes_exception_treated_as_failed_step():
    hermes = MagicMock()
    hermes.route = AsyncMock(side_effect=ConnectionError("hermes offline"))
    hermes.aclose = AsyncMock()

    settings = Settings(hermes_mode="auto")
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="LLM answer after Hermes crashed")
    llm.aclose = AsyncMock()
    storage = MagicMock()

    orch = Orchestrator(settings=settings, llm=llm, storage=storage)
    orch._hermes = hermes  # type: ignore[attr-defined]

    out = asyncio.run(orch.run(role="chat", task="hi"))
    assert out == "LLM answer after Hermes crashed"
    chain = orch.last_chain()
    assert chain[0].provider == "hermes"
    assert chain[0].ok is False
    assert "exception" in chain[0].reason.lower()


# ---------------- 5) Health snapshot ----------------

def test_health_returns_shape_used_by_status():
    orch, _, _ = _make_orchestrator(hermes_answer=None, llm_side_effect="ok")
    h = asyncio.run(orch.health())
    for k in (
        "hermes_mode",
        "llm_primary_set",
        "llm_fallback_set",
        "recent_errors",
        "last_success_ts",
        "last_chain",
    ):
        assert k in h


def test_running_orchestrator_marks_last_success_ts():
    orch, _, _ = _make_orchestrator(
        hermes_answer="via hermes", llm_side_effect="via llm"
    )
    assert orch.last_success_ts is None
    asyncio.run(orch.run(role="chat", task="hi"))
    assert orch.last_success_ts is not None
