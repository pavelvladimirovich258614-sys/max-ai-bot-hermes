"""Tests for the Batch-3 pipeline orchestrator (Sub-task A).

Three smoke tests cover the contract from the spec:
  1. Simple topic with enough findings тЖТ no enrichment.
  2. PARTIAL cascade result тЖТ enrichment is requested.
  3. Hermes binary missing тЖТ enrichment is gracefully skipped.

The F2 cascade is mocked (we test pipeline behaviour, not cascade).
Hermes subprocess is mocked via monkeypatch / os.environ so the test
never actually spawns a process.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.pipeline_orchestrator import (
    PipelineOrchestrator,
    should_enrich,
)
from app.core.pipeline_state import (
    PipelineContext,
    PipelineState,
    can_transition,
    is_terminal,
)
from app.schemas.research import KeyFinding, ResearchResult


# ---- helpers ----

def _finding(url: str = "https://example.com/x") -> KeyFinding:
    return KeyFinding(
        claim="c", evidence="e", url=url, source_type="blog",
        published_at=date(2026, 8, 20), tier=2, confidence="high",
    )


def _cascade_result(status: str = "OK", findings: int = 3) -> ResearchResult:
    return ResearchResult(
        status=status,  # type: ignore[arg-type]
        freshness_window="30d",
        searched_at="2026-08-22T00:00:00+00:00",
        answer=f"answer for {findings} findings",
        key_findings=[_finding() for _ in range(findings)],
        unknowns=[],
        content_angles=[],
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        # Anything the orchestrator might need; kept minimal.
    )


# ---- FSM unit tests (bonus, support DoD тЙе3) ----

def test_fsm_transitions_are_explicit():
    """INIT can only go to RESEARCHING / FAILED / CANCELLED тАФ not DONE."""
    assert can_transition(PipelineState.INIT, PipelineState.RESEARCHING)
    assert can_transition(PipelineState.INIT, PipelineState.FAILED)
    assert can_transition(PipelineState.INIT, PipelineState.CANCELLED)
    assert not can_transition(PipelineState.INIT, PipelineState.DONE)
    assert not can_transition(PipelineState.INIT, PipelineState.ANALYZING)
    # terminal states have no outgoing edges
    assert not can_transition(PipelineState.DONE, PipelineState.INIT)
    assert is_terminal(PipelineState.DONE)
    assert is_terminal(PipelineState.FAILED)
    assert is_terminal(PipelineState.CANCELLED)


def test_should_enrich_triggers_on_partial():
    ctx = PipelineContext(topic="ai legal", cascade_result=_cascade_result("PARTIAL", 2).model_dump())
    assert should_enrich(ctx)


def test_should_enrich_triggers_on_compare_keyword():
    ctx = PipelineContext(topic="Compare X vs Y", cascade_result=_cascade_result("OK", 5).model_dump())
    assert should_enrich(ctx)


def test_should_enrich_skips_when_sufficient():
    ctx = PipelineContext(topic="hotfix smoke", cascade_result=_cascade_result("OK", 5).model_dump())
    assert not should_enrich(ctx)


# ---- DoD-required orchestrator tests ----

@pytest.mark.asyncio
async def test_pipeline_runs_simple_topic():
    """3+ findings, OK status тЖТ enrichment is skipped (not_needed)."""
    cascade = MagicMock()
    cascade.run = AsyncMock(return_value=_cascade_result("OK", findings=5))

    orch = PipelineOrchestrator(
        settings=_settings(),  # type: ignore[arg-type]
        cascade=cascade,
        hermes_bin="/nonexistent/binary",
    )
    ctx = await orch.run(topic="hotfix smoke", user_id=42)

    assert ctx.state == PipelineState.DONE
    assert ctx.user_id == 42
    assert ctx.cascade_result is not None
    assert ctx.cascade_result["status"] == "OK"
    assert ctx.enrichment is not None
    assert ctx.enrichment["status"] == "skipped"
    assert ctx.enrichment["reason"] == "not_needed"
    assert ctx.analysis  # synthesised, non-empty
    assert ctx.completed_at is not None
    cascade.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_enriches_when_cascade_partial():
    """PARTIAL cascade тЖТ orchestrator calls hermes_enrich (status=skipped if no bin)."""
    cascade = MagicMock()
    cascade.run = AsyncMock(return_value=_cascade_result("PARTIAL", findings=1))

    orch = PipelineOrchestrator(
        settings=_settings(),  # type: ignore[arg-type]
        cascade=cascade,
        hermes_bin="/nonexistent/binary",
    )
    ctx = await orch.run(topic="analyze the trend", user_id=0)

    assert ctx.state == PipelineState.DONE
    assert ctx.cascade_result is not None
    assert ctx.cascade_result["status"] == "PARTIAL"
    # With bin missing, enrichment is "skipped" with reason hermes_bin_missing
    assert ctx.enrichment is not None
    assert ctx.enrichment["status"] == "skipped"
    assert ctx.enrichment["reason"] == "hermes_bin_missing"


@pytest.mark.asyncio
async def test_pipeline_skips_enrichment_when_hermes_unavailable():
    """Even when should_enrich is True, missing hermes bin is graceful."""
    cascade = MagicMock()
    cascade.run = AsyncMock(return_value=_cascade_result("PARTIAL", findings=1))

    orch = PipelineOrchestrator(
        settings=_settings(),  # type: ignore[arg-type]
        cascade=cascade,
        hermes_bin="/no/such/binary/anywhere",
    )
    ctx = await orch.run(topic="compare A vs B", user_id=0)

    # Final state is still DONE (graceful degradation)
    assert ctx.state == PipelineState.DONE
    assert ctx.enrichment is not None
    assert ctx.enrichment["status"] == "skipped"
    # No crash, no FAILED
    assert "FAILED" not in ctx.errors
    assert ctx.errors == []


# ---- optional bonus: subprocess success path ----

@pytest.mark.asyncio
async def test_pipeline_records_enrichment_when_hermes_returns_json(monkeypatch):
    """Happy path: hermes subprocess returns valid JSON тЖТ status=applied."""
    cascade = MagicMock()
    cascade.run = AsyncMock(return_value=_cascade_result("PARTIAL", findings=1))

    # Patch _hermes_bin_available to True and _hermes_enrich internals via
    # mocking subprocess. Easier: patch the module-level method.
    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b'{"answer":"deep analysis"}', b""))
    fake_proc.returncode = 0
    fake_proc.kill = MagicMock()
    fake_proc.wait = AsyncMock()

    async def _fake_subprocess_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subprocess_exec)

    orch = PipelineOrchestrator(
        settings=_settings(),  # type: ignore[arg-type]
        cascade=cascade,
        hermes_bin="/usr/bin/hermes",
    )
    # Force available
    monkeypatch.setattr(orch, "_hermes_bin_available", lambda: True)

    ctx = await orch.run(topic="deep analysis", user_id=0)
    assert ctx.state == PipelineState.DONE
    assert ctx.enrichment is not None
    assert ctx.enrichment["status"] == "applied"
    assert "deep analysis" in ctx.analysis
