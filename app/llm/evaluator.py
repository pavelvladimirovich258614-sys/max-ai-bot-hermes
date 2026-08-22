"""Editor-factcheck evaluator (B3, 2026-08-22).

Two-stage quality gate for a /research artifact:

  1) Factcheck (deterministic) — runs the five default criteria against the
     artifact's ``key_findings``:
       - all_facts_cited     (every finding has an http(s) URL)
       - published_at_recent (every finding is within the freshness window)
       - has_primary_source  (at least one source_type=primary)
       - confidence_labeled  (every finding has confidence=high|medium|low)
       - no_duplicate_claims (no two findings have the same claim text)

  2) Editor (LLM-backed) — asks the LLM to score the artifact along the
     six ``SCORE_AXES`` (0..10 each). The call goes through the existing
     ``LLMClient.chat()``; if the LLM call fails or returns invalid JSON,
     we fall back to deterministic scores so the caller always gets a
     well-formed ``EvalOutput``.

Final status:
  - APPROVED          iff every criterion passed AND no critical issues
                       AND every score >= 7.
  - REVISION_REQUIRED otherwise.

The wrapper is read-only: it never mutates the artifact, never calls
/research, never persists anything. The orchestrator decides what to do
with the verdict.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from app.llm.client import LLMClient
from app.llm.evaluator_schemas import (
    SCORE_AXES,
    EvalInput,
    EvalOutput,
)

logger = logging.getLogger("maxbot.evaluator")


# Default criteria for "research" artifacts. Other artifact types ("copy",
# "plan") use an empty list and rely on the LLM editor alone.
DEFAULT_RESEARCH_CRITERIA: list[str] = [
    "all_facts_cited",
    "published_at_recent",
    "has_primary_source",
    "confidence_labeled",
    "no_duplicate_claims",
]

# Status threshold — every axis must be at or above this for APPROVED.
APPROVE_MIN_SCORE = 7

# How old a finding may be (in days) before we flag it as stale. The artifact
# may also carry its own ``freshness_window`` ("7d"/"30d"/"90d"/"all"); when
# present we honour that. The 365-day ceiling is a safety net for "all" /
# missing / unknown values so a single bad row never inflates the window.
_FALLBACK_FRESHNESS_DAYS = 365


class EvaluatorError(RuntimeError):
    """Raised when the LLM response cannot be parsed into valid scores."""


class ResearchEvaluator:
    """Editor + factcheck wrapper around ``LLMClient``."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    # ------------------------------------------------------------------ public

    async def evaluate(self, eval_input: EvalInput) -> EvalOutput:
        """Run the factcheck + editor pass and return the merged verdict."""
        criteria = self._resolve_criteria(eval_input)
        findings = self._extract_findings(eval_input.artifact)
        critical_issues: list[str] = []
        required_changes: list[str] = []
        checklist: dict[str, bool] = {}

        if "all_facts_cited" in criteria:
            ok, issue, change = self._check_all_facts_cited(findings)
            checklist["all_facts_cited"] = ok
            if issue:
                critical_issues.append(issue)
            if change:
                required_changes.append(change)

        if "published_at_recent" in criteria:
            fw_days = self._freshness_days_from(eval_input.artifact)
            ok, issue, change = self._check_published_at_recent(findings, fw_days)
            checklist["published_at_recent"] = ok
            if issue:
                critical_issues.append(issue)
            if change:
                required_changes.append(change)

        if "has_primary_source" in criteria:
            ok, issue, change = self._check_has_primary_source(findings)
            checklist["has_primary_source"] = ok
            if issue:
                critical_issues.append(issue)
            if change:
                required_changes.append(change)

        if "confidence_labeled" in criteria:
            ok, issue, change = self._check_confidence_labeled(findings)
            checklist["confidence_labeled"] = ok
            if issue:
                critical_issues.append(issue)
            if change:
                required_changes.append(change)

        if "no_duplicate_claims" in criteria:
            ok, issue, change = self._check_no_duplicate_claims(findings)
            checklist["no_duplicate_claims"] = ok
            if issue:
                critical_issues.append(issue)
            if change:
                required_changes.append(change)

        scores = await self._llm_scores(eval_input, criteria, checklist)

        all_pass = all(checklist.values()) if checklist else True
        min_score = min(scores.values()) if scores else 0
        status: str
        if all_pass and not critical_issues and min_score >= APPROVE_MIN_SCORE:
            status = "APPROVED"
        else:
            status = "REVISION_REQUIRED"

        return EvalOutput(
            status=status,  # type: ignore[arg-type]
            scores=scores,
            critical_issues=critical_issues,
            required_changes=required_changes,
            final_checklist=checklist,
        )

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _resolve_criteria(eval_input: EvalInput) -> list[str]:
        if eval_input.criteria is not None:
            return list(eval_input.criteria)
        if eval_input.artifact_type == "research":
            return list(DEFAULT_RESEARCH_CRITERIA)
        return []

    @staticmethod
    def _extract_findings(artifact: dict[str, Any]) -> list[dict[str, Any]]:
        raw = artifact.get("key_findings") or []
        out: list[dict[str, Any]] = []
        for f in raw:
            if isinstance(f, dict):
                out.append(f)
        return out

    @staticmethod
    def _freshness_days_from(artifact: dict[str, Any]) -> int:
        """Read ``artifact['freshness_window']`` and map it to a day budget.

        Accepts the same values the cascade emits ("7d"/"30d"/"90d"/"all").
        Unknown / missing values fall back to 365 days as a generous ceiling.
        """
        from app.schemas.research import freshness_to_days

        fw = artifact.get("freshness_window")
        days = freshness_to_days(fw)  # type: ignore[arg-type]
        if days is None:
            return _FALLBACK_FRESHNESS_DAYS
        return days

    # ---- single-criterion checks (each returns (ok, issue, change)) ----

    @staticmethod
    def _check_all_facts_cited(
        findings: list[dict[str, Any]],
    ) -> tuple[bool, str | None, str | None]:
        if not findings:
            return True, None, None
        missing = [
            f for f in findings
            if not (f.get("url") or "").startswith(("http://", "https://"))
        ]
        if not missing:
            return True, None, None
        n = len(missing)
        return (
            False,
            f"{n} finding(s) missing http(s) URL",
            "Add a direct source URL to every key finding",
        )

    @staticmethod
    def _check_published_at_recent(
        findings: list[dict[str, Any]], fw_days: int
    ) -> tuple[bool, str | None, str | None]:
        if not findings:
            return True, None, None
        cutoff = date.today() - timedelta(days=fw_days)
        stale: list[str] = []
        for f in findings:
            pub = f.get("published_at")
            pub_date: date | None
            if isinstance(pub, str):
                try:
                    pub_date = date.fromisoformat(pub)
                except ValueError:
                    pub_date = None
            elif isinstance(pub, date):
                pub_date = pub
            else:
                pub_date = None
            if pub_date is None or pub_date < cutoff:
                stale.append(str(f.get("claim", "?"))[:60])
        if not stale:
            return True, None, None
        n = len(stale)
        return (
            False,
            f"{n} finding(s) outside the {fw_days}-day freshness window",
            "Replace stale findings or extend the freshness window",
        )

    @staticmethod
    def _check_has_primary_source(
        findings: list[dict[str, Any]],
    ) -> tuple[bool, str | None, str | None]:
        if not findings:
            return False, "no findings at all", "Produce at least one finding"
        has_primary = any(
            (f.get("source_type") or "") == "primary" for f in findings
        )
        if has_primary:
            return True, None, None
        return (
            False,
            "no source_type=primary finding present",
            "Include at least one primary source in the findings",
        )

    @staticmethod
    def _check_confidence_labeled(
        findings: list[dict[str, Any]],
    ) -> tuple[bool, str | None, str | None]:
        if not findings:
            return True, None, None
        valid = {"high", "medium", "low"}
        unlabeled = [
            f for f in findings
            if (f.get("confidence") or "") not in valid
        ]
        if not unlabeled:
            return True, None, None
        n = len(unlabeled)
        return (
            False,
            f"{n} finding(s) without a valid confidence label",
            "Label every finding with confidence=high|medium|low",
        )

    @staticmethod
    def _check_no_duplicate_claims(
        findings: list[dict[str, Any]],
    ) -> tuple[bool, str | None, str | None]:
        if not findings:
            return True, None, None
        seen: set[str] = set()
        dups: list[str] = []
        for f in findings:
            k = (f.get("claim") or "").strip().lower()
            if not k:
                continue
            if k in seen:
                dups.append(f.get("claim") or k)
            seen.add(k)
        if not dups:
            return True, None, None
        n = len(dups)
        return (
            False,
            f"{n} duplicate claim(s) detected",
            "Deduplicate the findings list",
        )

    # ---- LLM-backed scoring ----

    async def _llm_scores(
        self,
        eval_input: EvalInput,
        criteria: list[str],
        checklist: dict[str, bool],
    ) -> dict[str, int]:
        """Ask the LLM to score 0..10 along the 6 axes.

        On any failure (LLM error, malformed JSON, out-of-range value) we
        fall back to deterministic scores derived from the checklist so the
        caller still receives a well-formed ``EvalOutput``.
        """
        prompt = (
            "You are a research editor. Score the artifact below along 6 axes "
            "from 0 to 10. Return ONLY a JSON object with these keys, in this "
            f"order: {list(SCORE_AXES)}.\n"
            f"artifact_type: {eval_input.artifact_type}\n"
            f"criteria: {criteria}\n"
            f"checklist (pre-computed): {json.dumps(checklist, ensure_ascii=False)}\n"
            "artifact:\n"
            f"{json.dumps(eval_input.artifact, ensure_ascii=False, default=str)[:6000]}"
        )
        try:
            text = await self._llm.chat(
                [{"role": "user", "content": prompt}],
                role="evaluator",
            )
            return _parse_score_payload(text)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "evaluator LLM scoring failed, using deterministic fallback: %s",
                e,
            )
            return _deterministic_scores(checklist, len(criteria))


def _parse_score_payload(text: str) -> dict[str, int]:
    """Extract the 6-axis score dict from the LLM response.

    Accepts either a raw JSON object or a ```json ... ``` fenced block.
    Raises ``EvaluatorError`` on anything else.
    """
    if not text:
        raise EvaluatorError("empty LLM response")
    s = text.strip()
    if s.startswith("```"):
        # Strip opening fence and optional "json" tag.
        s = s.strip("`").lstrip()
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
        # Strip any closing fence left over.
        if s.endswith("```"):
            s = s[:-3].rstrip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise EvaluatorError(f"LLM did not return valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise EvaluatorError(
            f"LLM JSON must be an object, got {type(data).__name__}"
        )
    scores: dict[str, int] = {}
    for axis in SCORE_AXES:
        if axis not in data:
            raise EvaluatorError(f"missing axis {axis!r} in LLM response")
        val = data[axis]
        if isinstance(val, bool) or not isinstance(val, int):
            raise EvaluatorError(
                f"axis {axis!r} must be int, got {type(val).__name__}"
            )
        if val < 0 or val > 10:
            raise EvaluatorError(
                f"axis {axis!r}={val} out of range [0, 10]"
            )
        scores[axis] = val
    return scores


def _deterministic_scores(
    checklist: dict[str, bool], n_criteria: int
) -> dict[str, int]:
    """Produce 6-axis scores without consulting the LLM.

    All six axes share the same value (a clean artifact gets 9, a flagged
    one gets 5). We do not try to be clever here — the point of the
    fallback is to keep ``EvalOutput`` well-formed, not to make nuanced
    editorial calls.
    """
    if not checklist:
        base = 7
    else:
        base = 9 if all(checklist.values()) else 5
    return {axis: base for axis in SCORE_AXES}
