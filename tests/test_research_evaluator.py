"""Tests for the editor-factcheck evaluator (B3, 2026-08-22).

The LLMClient is always mocked — these tests assert the contract of the
wrapper (deterministic factcheck + score parsing + status decision), never
the behaviour of the LLM itself.

Test inventory (B3 spec, exactly 4 tests):

  test_evaluator_validates_score_ranges    Pydantic-level guard for scores
  test_evaluator_approves_clean_research   happy path -> APPROVED
  test_evaluator_flags_unsourced_claim     missing URL -> REVISION_REQUIRED
  test_evaluator_golden_set_5_topics       structural check of the fixture
                                           (no LLM call, no factcheck run)

The golden-set test is marked with ``@pytest.mark.evaluator`` so it does not
run by default. Run it with ``pytest -m evaluator`` when the production
LLM key is wired up.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.llm.evaluator import (
    APPROVE_MIN_SCORE,
    ResearchEvaluator,
)
from app.llm.evaluator_schemas import (
    SCORE_AXES,
    EvalInput,
    EvalOutput,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


GOLDEN_SET_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "eval_golden_set.json"
)


def _good_scores() -> dict[str, int]:
    """A 6-axis score map that always passes the APPROVED threshold."""
    return {axis: 9 for axis in SCORE_AXES}


def _mock_llm(scores: dict[str, int] | None = None) -> MagicMock:
    """Return a MagicMock that quacks like an LLMClient.

    ``chat()`` returns the JSON-encoded ``scores`` dict wrapped in a
    ```json ... ``` fence so we exercise the parser's stripping logic too.
    """
    llm = MagicMock()
    payload = scores if scores is not None else _good_scores()
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    llm.chat = AsyncMock(return_value=fenced)
    return llm


def _build_artifact(**overrides: Any) -> dict[str, Any]:
    """A clean research artifact that should sail through every criterion."""
    base: dict[str, Any] = {
        "status": "OK",
        "freshness_window": "30d",
        "searched_at": "2026-08-22T12:00:00+00:00",
        "answer": "Two-sentence summary of the topic.",
        "key_findings": [
            {
                "claim": "Claim A is supported by a primary source.",
                "evidence": "Quote from source A.",
                "url": "https://example.com/a",
                "source_type": "primary",
                "published_at": "2026-08-15",
                "tier": 1,
                "confidence": "high",
            },
            {
                "claim": "Claim B is supported by reputable media.",
                "evidence": "Quote from source B.",
                "url": "https://example.com/b",
                "source_type": "reputable_media",
                "published_at": "2026-08-10",
                "tier": 2,
                "confidence": "medium",
            },
        ],
        "unknowns": [],
        "content_angles": ["angle 1"],
        "tier_summary": {
            "tier1_urls": 1,
            "tier2_crawled": 1,
            "tier3_verified": 2,
            "hermes_enrichment": "skipped",
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# test 1: Pydantic-level score validation
# ---------------------------------------------------------------------------


def test_evaluator_validates_score_ranges() -> None:
    """EvalOutput must reject scores outside [0, 10] and non-integer values."""
    # Out-of-range high
    with pytest.raises(ValidationError) as exc:
        EvalOutput(
            status="APPROVED",
            scores={
                "factuality": 11,
                "freshness": 5,
                "source_quality": 5,
                "confidence_labeling": 5,
                "uniqueness": 5,
                "overall": 5,
            },
        )
    assert "out of range" in str(exc.value)

    # Out-of-range low
    with pytest.raises(ValidationError) as exc:
        EvalOutput(
            status="APPROVED",
            scores={
                "factuality": -1,
                "freshness": 5,
                "source_quality": 5,
                "confidence_labeling": 5,
                "uniqueness": 5,
                "overall": 5,
            },
        )
    assert "out of range" in str(exc.value)

    # Non-integer (float) value — Pydantic v2 does NOT coerce float to int.
    with pytest.raises(ValidationError) as exc:
        EvalOutput(
            status="APPROVED",
            scores={
                "factuality": 9.5,  # type: ignore[dict-item]
                "freshness": 5,
                "source_quality": 5,
                "confidence_labeling": 5,
                "uniqueness": 5,
                "overall": 5,
            },
        )
    assert "must be int" in str(exc.value) or "int" in str(exc.value)

    # Wrong number of axes
    with pytest.raises(ValidationError) as exc:
        EvalOutput(
            status="APPROVED",
            scores={"factuality": 5, "freshness": 5},  # only 2 axes
        )
    assert "exactly 6 axes" in str(exc.value)

    # Valid range still works
    out = EvalOutput(
        status="APPROVED",
        scores=_good_scores(),
    )
    assert all(0 <= v <= 10 for v in out.scores.values())


# ---------------------------------------------------------------------------
# test 2: clean research -> APPROVED
# ---------------------------------------------------------------------------


def test_evaluator_approves_clean_research() -> None:
    """A clean research artifact should pass every criterion and get APPROVED."""
    artifact = _build_artifact()
    llm = _mock_llm()
    ev = ResearchEvaluator(llm)

    out = asyncio.run(ev.evaluate(EvalInput(artifact_type="research", artifact=artifact)))

    assert out.status == "APPROVED"
    assert all(out.final_checklist.values()), (
        f"clean artifact must pass every criterion; got {out.final_checklist}"
    )
    assert out.critical_issues == []
    assert out.required_changes == []
    assert all(v >= APPROVE_MIN_SCORE for v in out.scores.values())
    # LLM was consulted exactly once for the editor score.
    assert llm.chat.await_count == 1


# ---------------------------------------------------------------------------
# test 3: unsourced claim -> REVISION_REQUIRED
# ---------------------------------------------------------------------------


def test_evaluator_flags_unsourced_claim() -> None:
    """A finding without a URL must be flagged, even if the LLM says all good."""
    artifact = _build_artifact(
        key_findings=[
            {
                "claim": "Unsourced claim — empty URL.",
                "evidence": "Some text without a link.",
                "url": "",  # <-- the defect
                "source_type": "blog",
                "published_at": "2026-08-15",
                "tier": 3,
                "confidence": "medium",
            },
        ],
    )
    llm = _mock_llm(_good_scores())  # LLM would approve; factcheck must not.
    ev = ResearchEvaluator(llm)

    out = asyncio.run(ev.evaluate(EvalInput(artifact_type="research", artifact=artifact)))

    assert out.status == "REVISION_REQUIRED"
    assert out.final_checklist["all_facts_cited"] is False
    assert any("URL" in issue for issue in out.critical_issues), (
        f"expected a URL-related critical issue, got {out.critical_issues}"
    )
    assert any("URL" in change for change in out.required_changes), (
        f"expected a URL-related change hint, got {out.required_changes}"
    )


# ---------------------------------------------------------------------------
# test 4: golden set — structural validation only (marked, opt-in)
# ---------------------------------------------------------------------------


@pytest.mark.evaluator
def test_evaluator_golden_set_5_topics() -> None:
    """The golden set must load as JSON and contain 5 well-formed topics.

    This is a *structural* test — it never calls the LLM and never runs
    the evaluator over the artifacts. A future manual run (or a CI job with
    the LLM key wired up) will exercise the full pipeline.
    """
    assert GOLDEN_SET_PATH.exists(), (
        f"golden set fixture missing at {GOLDEN_SET_PATH}"
    )
    raw = GOLDEN_SET_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert isinstance(data, list), "golden set must be a JSON array"
    assert len(data) == 5, f"golden set must have 5 topics, got {len(data)}"

    required = {
        "topic",
        "expected_min_findings",
        "expected_tier_min",
        "expected_confidence",
        "banned_phrases",
    }
    valid_conf = {"high", "medium", "low"}

    for idx, entry in enumerate(data):
        assert isinstance(entry, dict), f"entry {idx} must be an object"
        missing = required - set(entry.keys())
        assert not missing, f"entry {idx} missing keys: {missing}"

        topic = entry["topic"]
        assert isinstance(topic, str) and topic.strip(), (
            f"entry {idx} topic must be a non-empty string"
        )

        emf = entry["expected_min_findings"]
        assert isinstance(emf, int) and emf >= 1, (
            f"entry {idx} expected_min_findings must be a positive int"
        )

        etm = entry["expected_tier_min"]
        assert isinstance(etm, int) and 1 <= etm <= 3, (
            f"entry {idx} expected_tier_min must be 1..3"
        )

        ec = entry["expected_confidence"]
        assert isinstance(ec, list) and ec, (
            f"entry {idx} expected_confidence must be a non-empty list"
        )
        for c in ec:
            assert c in valid_conf, (
                f"entry {idx} confidence {c!r} not in {valid_conf}"
            )

        bp = entry["banned_phrases"]
        assert isinstance(bp, list) and bp, (
            f"entry {idx} banned_phrases must be a non-empty list"
        )
        for phrase in bp:
            assert isinstance(phrase, str) and phrase.strip(), (
                f"entry {idx} banned phrase must be a non-empty string"
            )
