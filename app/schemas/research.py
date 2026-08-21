"""Strict JSON schemas for the /research output (F2.3, 2026-08-21).

The /research command MUST emit a JSON object that validates against
``ResearchResult``. Anything else (free-form prose, malformed JSON,
missing fields) is rejected and falls into ``status="FAILED"`` in the
caller. We never silently fall back to "красивый текст" — the whole
point of the schema is to make the LLM produce something the bot can
parse and act on.

Schema overview (F2.3, do not change without updating the role_prompt
and the test suite together):

  ResearchResult:
    status            OK | NO_FRESH_DATA | PARTIAL | FAILED
    freshness_window  7d | 30d | 90d | all
    searched_at       ISO-8601
    answer            3-5 sentences summary
    key_findings      list[KeyFinding]
    unknowns          list[str]
    content_angles    list[str]
    tier_summary      dict with tier1/2/3 counts + hermes_enrichment

  KeyFinding:
    claim         verifiable statement
    evidence      quote or paraphrase
    url           https://...
    source_type   primary | official | reputable_media | blog | forum
    published_at  YYYY-MM-DD
    tier          1 | 2 | 3
    confidence    high | medium | low
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---- enums (kept as Literal strings so JSON output is short and human-readable) ----

FreshnessWindow = Literal["7d", "30d", "90d", "all"]
SourceType = Literal["primary", "official", "reputable_media", "blog", "forum"]
TierNumber = Literal[1, 2, 3]
Confidence = Literal["high", "medium", "low"]
ResearchStatus = Literal["OK", "NO_FRESH_DATA", "PARTIAL", "FAILED"]


# ---- core models ----


class KeyFinding(BaseModel):
    """One verifiable claim with its evidence chain."""

    claim: str = Field(..., min_length=1, description="Проверяемое утверждение")
    evidence: str = Field(default="", description="Цитата или пересказ")
    url: str = Field(..., min_length=1, description="Прямой URL источника")
    source_type: SourceType = Field(..., description="Тип источника")
    published_at: date = Field(..., description="Дата публикации YYYY-MM-DD")
    tier: TierNumber = Field(..., description="Tier 1/2/3 — где найдено")
    confidence: Confidence = Field(..., description="Уровень уверенности")

    @field_validator("url")
    @classmethod
    def _url_must_be_http(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"KeyFinding.url must be http(s), got {v!r}")
        return v


class TierSummary(BaseModel):
    """Bookkeeping for the cascade — what each tier produced."""

    tier1_urls: int = Field(default=0, ge=0, description="URL'ов найдено в Tier 1")
    tier2_crawled: int = Field(default=0, ge=0, description="URL'ов crawled в Tier 2")
    tier3_verified: int = Field(default=0, ge=0, description="Findings, прошедшие Tier 3")
    hermes_enrichment: Literal["applied", "skipped", "failed"] = Field(
        default="skipped",
        description="Статус Hermes subprocess enrichment",
    )


class ResearchResult(BaseModel):
    """Top-level /research response. Anything else is a hard error."""

    status: ResearchStatus
    freshness_window: FreshnessWindow
    searched_at: str = Field(..., description="ISO-8601 timestamp")
    answer: str = Field(default="", description="3-5 предложений summary")
    key_findings: list[KeyFinding] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    content_angles: list[str] = Field(default_factory=list)
    tier_summary: TierSummary = Field(default_factory=TierSummary)

    @field_validator("searched_at")
    @classmethod
    def _searched_at_iso8601(cls, v: str) -> str:
        # Permissive: accept anything with a "T" (datetime.isoformat) or
        # space (common MAX-format). We don't parse strictly because the
        # LLM might emit "2026-08-21T15:30:45+08:00" or "2026-08-21 15:30:45".
        if "T" not in v and " " not in v:
            raise ValueError(f"searched_at must be ISO-8601, got {v!r}")
        return v

    def to_compact_json(self) -> str:
        """Return a compact JSON string for log files and smoke-tests."""
        # Pydantic v2 model_dump_json() returns compact JSON by default
        # when no ``indent`` argument is passed.
        return self.model_dump_json()


# ---- helpers ----


def days_to_date(days: int) -> date:
    """Return today - ``days`` as a ``date`` (used for ``after_date`` filters)."""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days)).date()


def parse_freshness(value: str) -> FreshnessWindow:
    """Normalize a user-supplied freshness string. Accepts "30d", "30 d", "30".

    Empty / unknown input -> the F2.5 default ("30d"). To get "all"
    the user must explicitly write "all" or "*".
    """
    s = (value or "").strip().lower().replace(" ", "")
    if s in ("7d", "7", "week"):
        return "7d"
    if s in ("30d", "30", "month", "1m"):
        return "30d"
    if s in ("90d", "90", "quarter", "3m"):
        return "90d"
    if s in ("all", "any", "*"):
        return "all"
    # Unknown value (including empty) -> F2.5 default.
    return "30d"


def freshness_to_days(window: FreshnessWindow) -> Optional[int]:
    """Map a freshness window to a number of days, or None for 'all'."""
    return {"7d": 7, "30d": 30, "90d": 90, "all": None}.get(window, 30)
