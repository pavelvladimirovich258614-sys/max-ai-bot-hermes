"""Strict Pydantic schemas for the editor-factcheck evaluator (B3, 2026-08-22).

The evaluator is the second pair of eyes on a /research artifact. The first
half is deterministic factchecking (URL present? fresh? primary? labeled?
unique?); the second half is the "editor" — an LLM call that produces a 6-axis
score breakdown. The two halves merge into a single ``EvalOutput`` so the
caller never has to do its own bookkeeping.

Schema contract (do not change without updating the role_prompt, the tests
and the 4 patch files in lockstep):

  EvalInput
    artifact_type  "research" | "copy" | "plan"
    artifact       free-form dict (the JSON we just produced for the user)
    criteria       optional list[str] — when None, evaluator uses
                   DEFAULT_RESEARCH_CRITERIA for "research" and an empty
                   list for "copy" / "plan" (the editor then scores
                   without factcheck pre-pass).

  EvalOutput
    status            "APPROVED" | "REVISION_REQUIRED"
    scores            dict[str, int]  — exactly 6 axes, each 0..10
    critical_issues   list[str]  — facts that MUST be fixed
    required_changes  list[str]  — concrete actions the author should take
    final_checklist   dict[str, bool]  — which criteria passed

The 6 score axes (hardcoded, in this order) are:
    factuality, freshness, source_quality, confidence_labeling,
    uniqueness, overall.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


ArtifactType = Literal["research", "copy", "plan"]
EvalStatus = Literal["APPROVED", "REVISION_REQUIRED"]


# Hardcoded axis names (must match SCORE_AXES in evaluator.py).
SCORE_AXES: tuple[str, ...] = (
    "factuality",
    "freshness",
    "source_quality",
    "confidence_labeling",
    "uniqueness",
    "overall",
)


class EvalInput(BaseModel):
    """What the caller hands to ResearchEvaluator.evaluate()."""

    artifact_type: ArtifactType
    artifact: dict = Field(default_factory=dict)
    criteria: list[str] | None = None


class EvalOutput(BaseModel):
    """The verdict the editor-factcheck layer returns."""

    status: EvalStatus
    scores: dict[str, int]
    critical_issues: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    final_checklist: dict[str, bool] = Field(default_factory=dict)

    @field_validator("scores")
    @classmethod
    def _validate_scores(cls, v: dict[str, int]) -> dict[str, int]:
        if len(v) != 6:
            raise ValueError(
                f"scores must have exactly 6 axes (got {len(v)}: {list(v)})"
            )
        for axis, val in v.items():
            if not isinstance(val, int) or isinstance(val, bool):
                # bool is a subclass of int in Python — reject it explicitly
                # so we never silently accept True/False as a score.
                raise ValueError(
                    f"score {axis!r} must be int, got {type(val).__name__}"
                )
            if val < 0 or val > 10:
                raise ValueError(
                    f"score {axis!r}={val} out of range [0, 10]"
                )
        return v

    @field_validator("final_checklist")
    @classmethod
    def _validate_checklist(cls, v: dict[str, bool]) -> dict[str, bool]:
        for name, ok in v.items():
            if not isinstance(ok, bool):
                raise ValueError(
                    f"checklist[{name!r}] must be bool, got {type(ok).__name__}"
                )
        return v
