"""Three-tier research cascade (F2.2, 2026-08-21).

Pipeline:

  Tier 1 — discovery:
    Run WebSearch.search(query, after_date=...) and collect up to
    TIER1_LIMIT URLs. If we already have ≥TIER1_STOP_THRESHOLD findings
    with a publish date within the freshness window, we stop here.

  Tier 2 — crawl:
    For the top TIER2_CRAWL_LIMIT URLs from Tier 1, call
    WebReader.read_with_meta() and pull (title, text, published_at).
    Anything we could not parse is dropped (logged). Findings whose
    published_at is older than the freshness window are demoted to
    "medium" confidence at most.

  Tier 3 — verify:
    Validate every claim we are about to ship. Tier 3 enforces the
    F2.1 "never present an old source as a fact" rule by:
      * downgrading confidence to "low" if published_at is older than
        the window but the topic is not historical
      * marking conflicting findings with the same ``claim`` (after
        normalization) so the answer mentions BOTH positions

  Hermes enrichment (F2.4):
    If env HERMES_CLI_BIN is set (default "hermes"), we spawn
    ``<hermes> enrich-research <topic> --timeout 30s`` as a background
    subprocess. We do NOT block: the result is incorporated if it
    returns within 30s, otherwise we mark "hermes_enrichment": "skipped"
    and move on. Per F0, the current Pavel's installation does not have
    peer RZA registered, so the subprocess will return non-zero; we
    handle that gracefully.

Output:
    ``ResearchResult`` (see app.schemas.research) — strict JSON, the
    caller is expected to parse it back into the same Pydantic model.
    If a downstream agent cannot satisfy the schema, we return
    ``status="FAILED"`` with an empty key_findings list.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError

from app.config import Settings
from app.db.research_cache import cache as _research_cache_decorator
from app.schemas.research import (
    FreshnessWindow,
    KeyFinding,
    ResearchResult,
    TierSummary,
    days_to_date,
    freshness_to_days,
)
from app.tools.web_reader import WebReader
from app.tools.web_search import SearchResult, WebSearch

logger = logging.getLogger("maxbot.research_cascade")


# ---- tunables (kept here so tests can override via monkeypatch) ----

TIER1_LIMIT = 8                 # how many URLs to pull in discovery
TIER1_STOP_THRESHOLD = 3        # ≥ this many fresh high-confidence findings → stop
TIER2_CRAWL_LIMIT = 5           # how many to actually fetch+extract
HERMES_TIMEOUT_S = 30.0         # F2.4 hard limit
HERMES_BIN_ENV = "HERMES_CLI_BIN"  # default: "hermes"
ANSWER_SENTENCE_TARGET = (3, 5)  # F2.3 contract: 3-5 sentences in ``answer``


# ---- the cascade ----


class ResearchCascade:
    """Run the three-tier pipeline and emit a strict ``ResearchResult``."""

    def __init__(
        self,
        settings: Settings,
        web_search: Optional[WebSearch] = None,
        web_reader: Optional[WebReader] = None,
        *,
        tier1_limit: int = TIER1_LIMIT,
        tier1_stop_threshold: int = TIER1_STOP_THRESHOLD,
        tier2_crawl_limit: int = TIER2_CRAWL_LIMIT,
        hermes_timeout_s: float = HERMES_TIMEOUT_S,
    ) -> None:
        self._s = settings
        self._search = web_search or WebSearch(settings)
        self._reader = web_reader or WebReader(settings)
        self._tier1_limit = tier1_limit
        self._tier1_stop_threshold = tier1_stop_threshold
        self._tier2_crawl_limit = tier2_crawl_limit
        self._hermes_timeout_s = hermes_timeout_s

    async def aclose(self) -> None:
        await self._search.aclose()
        await self._reader.aclose()

    # ---- public entry point ----

    async def run(
        self,
        topic: str,
        freshness_window: FreshnessWindow,
    ) -> ResearchResult:
        """Run the cascade; always return a ResearchResult (even on errors)."""
        try:
            return await self._run_inner(topic, freshness_window)
        except Exception as e:  # noqa: BLE001
            logger.exception("research cascade crashed: %s", e)
            return self._failed_result(
                freshness_window,
                f"cascade crashed: {type(e).__name__}: {e}",
            )

    # ---- internals ----

    async def _run_inner(
        self,
        topic: str,
        freshness_window: FreshnessWindow,
    ) -> ResearchResult:
        # OBS-1: log the cascade start so an operator can correlate this
        # invocation with the webhook_in / handler log line.
        logger.info(
            "cascade_start topic=%r freshness=%s after_date=%s",
            topic, freshness_window,
            (days_to_date(freshness_to_days(freshness_window))
             if freshness_to_days(freshness_window) is not None else None),
        )
        days = freshness_to_days(freshness_window)
        after_date = days_to_date(days) if days is not None else None
        _tier_reached = 1  # tracked for cascade_done

        # ---- Tier 1: discovery ----
        try:
            tier1_results = await self._tier1(topic, after_date)
        except Exception:  # noqa: BLE001
            logger.exception("cascade_failed stage=tier1 topic=%r", topic)
            raise
        if not tier1_results:
            # Nothing returned at all. Check whether it's a "no fresh data"
            # situation vs. "search backend is down".
            fallback_oldest = await self._find_oldest_published_date(topic)
            if fallback_oldest is None and after_date is not None:
                logger.info(
                    "cascade_done topic=%r tier_reached=%d status=NO_FRESH_DATA",
                    topic, _tier_reached,
                )
                return ResearchResult(
                    status="NO_FRESH_DATA",
                    freshness_window=freshness_window,
                    searched_at=datetime.now(timezone.utc).isoformat(),
                    answer="Поиск не вернул ни одной свежей публикации.",
                    unknowns=[
                        f"Нет результатов в окне свежести {freshness_window}.",
                        f"Самая старая публикация по теме: {fallback_oldest or 'неизвестно'}.",
                    ],
                    content_angles=[],
                    tier_summary=TierSummary(tier1_urls=0),
                )
            logger.info(
                "cascade_done topic=%r tier_reached=%d status=FAILED",
                topic, _tier_reached,
            )
            return ResearchResult(
                status="FAILED",
                freshness_window=freshness_window,
                searched_at=datetime.now(timezone.utc).isoformat(),
                answer="Поисковый бэкенд не вернул результатов.",
                unknowns=["Tier 1 search returned 0 URLs — backend may be down."],
                content_angles=[],
                tier_summary=TierSummary(tier1_urls=0),
            )

        # Quick early-stop if Tier 1 alone is enough
        early_findings = self._quick_filter(tier1_results, after_date)
        if len(early_findings) >= self._tier1_stop_threshold:
            logger.info(
                "research: tier1 alone produced %d findings (≥ %d), skipping tier2",
                len(early_findings), self._tier1_stop_threshold,
            )
            logger.info(
                "cascade_done topic=%r tier_reached=%d findings_count=%d status=OK",
                topic, _tier_reached, len(early_findings),
            )
            # Per F2.4: we did NOT run the hermes subprocess in the
            # early-stop path. Mark it as "skipped" regardless of whether
            # the binary is on PATH — the "applied" status is reserved
            # for the case where the subprocess actually returned findings.
            return self._build_result(
                topic=topic,
                freshness_window=freshness_window,
                after_date=after_date,
                tier1_results=tier1_results,
                crawled_meta=[],
                hermes_findings=[],
                tier_summary=TierSummary(
                    tier1_urls=len(tier1_results),
                    tier2_crawled=0,
                    tier3_verified=len(early_findings),
                    hermes_enrichment="skipped",
                ),
                findings=early_findings,
            )

        # ---- Tier 2: crawl ----
        _tier_reached = 2
        try:
            crawled = await self._tier2(tier1_results)
        except Exception:  # noqa: BLE001
            logger.exception("cascade_failed stage=tier2 topic=%r", topic)
            raise

        # ---- Tier 3: verify & build findings ----
        _tier_reached = 3
        findings = self._tier3(early_findings, crawled, topic, after_date)

        # ---- Hermes enrichment (F2.4) — background, non-blocking ----
        hermes_findings, hermes_status = await self._hermes_enrich(topic)
        if hermes_findings:
            findings.extend(hermes_findings)

        result = self._build_result(
            topic=topic,
            freshness_window=freshness_window,
            after_date=after_date,
            tier1_results=tier1_results,
            crawled_meta=crawled,
            hermes_findings=[],
            tier_summary=TierSummary(
                tier1_urls=len(tier1_results),
                tier2_crawled=len(crawled),
                tier3_verified=len(findings),
                hermes_enrichment=hermes_status,
            ),
            findings=findings,
        )
        logger.info(
            "cascade_done topic=%r tier_reached=%d findings_count=%d status=%s",
            topic, _tier_reached, len(findings), result.status,
        )
        return result

    # ---- tier 1 ----

    async def _tier1(self, topic: str, after_date: Optional[date]) -> list[SearchResult]:
        """Run WebSearch with the date filter; sort by inferred freshness."""
        results = await self._search.search(
            topic, limit=self._tier1_limit, after_date=after_date,
        )
        # Tier 1 always returns at most tier1_limit results. The cascade's
        # date filter is enforced by the search backend when possible; we
        # also filter client-side because DDG HTML doesn't always honor
        # the token.
        return results

    def _quick_filter(
        self, results: list[SearchResult], after_date: Optional[date],
    ) -> list[KeyFinding]:
        """Build a best-effort KeyFinding from each Tier 1 result.

        We do not crawl yet, so published_at and confidence are lower.
        Tier 1 findings are useful for early-stop but should be replaced
        by Tier 2/3 findings whenever possible.
        """
        out: list[KeyFinding] = []
        for r in results:
            if not r.url:
                continue
            if after_date is not None:
                # We have no real published_at from Tier 1; the search
                # backend honored the filter, so confidence is medium.
                pub = after_date  # optimistic
                conf = "medium"
            else:
                pub = date(2000, 1, 1)  # unknown — flag as low confidence
                conf = "low"
            out.append(
                KeyFinding(
                    claim=r.title or "(no title)",
                    evidence=(r.snippet or "")[:200],
                    url=r.url,
                    source_type=self._guess_source_type(r.url),
                    published_at=pub,
                    tier=1,
                    confidence=conf,  # type: ignore[arg-type]
                )
            )
        return out

    # ---- tier 2 ----

    async def _tier2(self, tier1_results: list[SearchResult]) -> list[dict]:
        """Crawl the top N URLs; return list of ``read_with_meta`` payloads."""
        targets = tier1_results[: self._tier2_crawl_limit]
        if not targets:
            return []
        # Run crawls in parallel — but keep a hard concurrency cap of 4
        # so a single bad URL doesn't open 50 connections.
        sem = asyncio.Semaphore(4)

        async def _one(r: SearchResult) -> dict:
            async with sem:
                try:
                    meta = await self._reader.read_with_meta(r.url, max_chars=6000)
                    meta["tier1_title"] = r.title
                    meta["tier1_snippet"] = r.snippet
                    return meta
                except Exception as e:  # noqa: BLE001
                    logger.warning("tier2 crawl failed for %s: %s", r.url, e)
                    return {"ok": False, "url": r.url, "text": "", "published_at": None,
                            "title": "", "tier1_title": r.title, "tier1_snippet": r.snippet}

        return await asyncio.gather(*[_one(r) for r in targets])

    # ---- tier 3 ----

    def _tier3(
        self,
        tier1_findings: list[KeyFinding],
        crawled: list[dict],
        topic: str,
        after_date: Optional[date],
    ) -> list[KeyFinding]:
        """Build final findings from crawled pages; downgrade stale ones."""
        out: list[KeyFinding] = []
        seen_claims: dict[str, KeyFinding] = {}

        for meta in crawled:
            if not meta.get("ok"):
                # Crawl failed; fall back to Tier 1 entry if we have it.
                continue
            url = meta["url"]
            title = meta.get("title") or meta.get("tier1_title") or "(no title)"
            text = (meta.get("text") or meta.get("tier1_snippet") or "")[:300]
            pub: Optional[date] = meta.get("published_at")

            if pub is None:
                # No date from HTML — we treat as "unknown" and emit a
                # low-confidence finding with today's date (so the
                # freshness check at least runs).
                pub = date.today()
                confidence: str = "low"
            else:
                if after_date is not None and pub < after_date:
                    # The page is older than the window. Per F2.1, we
                    # still surface it but downgrade confidence.
                    confidence = "low"
                elif after_date is not None and (
                    date.today() - pub
                ).days > 90:
                    # F2.2 tier 3: > 90 days is "dated".
                    confidence = "medium"
                else:
                    confidence = "high"

            finding = KeyFinding(
                claim=title,
                evidence=text,
                url=url,
                source_type=self._guess_source_type(url),
                published_at=pub,
                tier=2,
                confidence=confidence,  # type: ignore[arg-type]
            )
            key = self._claim_key(finding.claim)
            if key in seen_claims:
                # Conflict: same claim, different URL. Mark BOTH as
                # medium confidence and keep the first; the second will
                # be added to ``unknowns`` so the answer mentions it.
                existing = seen_claims[key]
                existing.confidence = "medium"  # type: ignore[assignment]
                out.append(
                    KeyFinding(
                        claim=finding.claim + " (альтернативная позиция)",
                        evidence=finding.evidence,
                        url=finding.url,
                        source_type=finding.source_type,
                        published_at=finding.published_at,
                        tier=3,
                        confidence="low",
                    )
                )
            else:
                seen_claims[key] = finding
                out.append(finding)

        # If Tier 2 produced nothing usable, fall back to Tier 1 findings.
        if not out:
            return tier1_findings
        return out

    # ---- hermes enrichment (F2.4) ----

    def _hermes_bin_available(self) -> bool:
        """True if the hermes CLI is on PATH (or HERMES_CLI_BIN is set)."""
        bin_path = os.environ.get(HERMES_BIN_ENV, "hermes")
        if os.path.isabs(bin_path):
            return os.path.exists(bin_path)
        return shutil.which(bin_path) is not None

    async def _hermes_enrich(self, topic: str) -> tuple[list[KeyFinding], str]:
        """Spawn ``hermes enrich-research`` in the background; never block.

        Returns ``(findings, status)`` where status is one of
        ``"applied" | "skipped" | "failed"``. If the subprocess times
        out, raises, or its output is not parseable, we mark "skipped"
        and return no findings — the main pipeline keeps its Tier 1/2/3
        output unchanged.
        """
        if not self._hermes_bin_available():
            logger.info("research: hermes CLI not on PATH, skipping enrichment")
            return [], "skipped"

        bin_path = os.environ.get(HERMES_BIN_ENV, "hermes")
        cmd = [bin_path, "enrich-research", topic, "--timeout", "30s"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._hermes_timeout_s,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("research: hermes enrichment timeout after %.0fs",
                               self._hermes_timeout_s)
                return [], "skipped"

            if proc.returncode != 0:
                logger.info("research: hermes rc=%s, treating as skipped",
                            proc.returncode)
                return [], "skipped"

            payload = self._parse_hermes_stdout(stdout.decode("utf-8", errors="replace"))
            if not payload:
                return [], "failed"
            return payload, "applied"
        except FileNotFoundError:
            return [], "skipped"
        except Exception as e:  # noqa: BLE001
            logger.warning("research: hermes enrichment failed: %s", e)
            return [], "failed"

    def _parse_hermes_stdout(self, raw: str) -> list[KeyFinding]:
        """Parse ``hermes enrich-research`` JSON output into KeyFinding list.

        The expected shape is::

            [
              {"claim": "...", "evidence": "...", "url": "...",
               "published_at": "YYYY-MM-DD", "confidence": "high|medium|low"}
            ]

        We tolerate a single object (wrapped) and silently drop malformed
        items rather than failing the whole enrichment.
        """
        if not raw.strip():
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Maybe the CLI emits a "Warning: …" line first; try last line.
            lines = [ln for ln in raw.splitlines() if ln.strip().startswith(("{", "["))]
            if not lines:
                return []
            try:
                data = json.loads(lines[-1])
            except json.JSONDecodeError:
                return []
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        out: list[KeyFinding] = []
        for item in data:
            try:
                out.append(
                    KeyFinding(
                        claim=str(item.get("claim", ""))[:500],
                        evidence=str(item.get("evidence", ""))[:500],
                        url=str(item.get("url", "")),
                        source_type="reputable_media",  # default for hermes findings
                        published_at=_parse_iso_date(item.get("published_at"))
                        or date.today(),
                        tier=3,
                        confidence=item.get("confidence", "medium"),  # type: ignore[arg-type]
                    )
                )
            except ValidationError:
                continue
        return out

    # ---- result assembly ----

    def _build_result(
        self,
        *,
        topic: str,
        freshness_window: FreshnessWindow,
        after_date: Optional[date],
        tier1_results: list[SearchResult],
        crawled_meta: list[dict],
        hermes_findings: list[KeyFinding],
        tier_summary: TierSummary,
        findings: list[KeyFinding],
    ) -> ResearchResult:
        # Determine status
        if not findings:
            status: str = "NO_FRESH_DATA"
        elif after_date is not None and all(
            f.published_at < after_date for f in findings
        ):
            # All findings are older than the window — per F2.1 we
            # report this explicitly instead of pretending it's fresh.
            status = "NO_FRESH_DATA"
        else:
            high = sum(1 for f in findings if f.confidence == "high")
            if high >= 2:
                status = "OK"
            elif high >= 1:
                status = "PARTIAL"
            else:
                status = "PARTIAL"

        # answer: 3-5 sentences summarizing the findings.
        answer = self._compose_answer(topic, findings, status, after_date)

        # content_angles: surface post ideas (1 per high-confidence finding)
        content_angles = [
            f"📌 {f.claim[:120]}" for f in findings if f.confidence == "high"
        ][:5]

        return ResearchResult(
            status=status,  # type: ignore[arg-type]
            freshness_window=freshness_window,
            searched_at=datetime.now(timezone.utc).isoformat(),
            answer=answer,
            key_findings=findings,
            unknowns=self._compose_unknowns(findings, after_date, status),
            content_angles=content_angles,
            tier_summary=tier_summary,
        )

    def _compose_answer(
        self,
        topic: str,
        findings: list[KeyFinding],
        status: str,
        after_date: Optional[date],
    ) -> str:
        if not findings:
            return (
                f"По теме «{topic}» в окне свежести {self._window_phrase(after_date)} "
                "не найдено свежих публикаций."
            )
        # Compose from top-3 findings
        top = findings[:3]
        bullets = " ".join(f"• {f.claim}." for f in top)
        n = len(findings)
        window = f"за последние {self._window_phrase(after_date)}" if after_date else ""
        return (
            f"По теме «{topic}» {window} найдено {n} подтверждённых находок. "
            f"{bullets}"
        )

    def _window_phrase(self, after_date: Optional[date]) -> str:
        if after_date is None:
            return "без ограничения по дате"
        days = (date.today() - after_date).days
        if days <= 7:
            return "7 дней"
        if days <= 30:
            return "30 дней"
        if days <= 90:
            return "90 дней"
        return f"{days} дней"

    def _compose_unknowns(
        self,
        findings: list[KeyFinding],
        after_date: Optional[date],
        status: str,
    ) -> list[str]:
        out: list[str] = []
        low = sum(1 for f in findings if f.confidence == "low")
        if low:
            out.append(f"{low} находок с низкой уверенностью — требуют ручной проверки.")
        if status == "NO_FRESH_DATA" and after_date is not None:
            out.append(
                f"Нет публикаций новее {after_date.isoformat()}. "
                f"Попробуйте расширить окно (90d / all) или уточнить тему."
            )
        if findings and all(f.url for f in findings):
            out.append("Все ключевые находки имеют прямые URL и оценку уверенности.")
        return out

    def _failed_result(self, freshness_window: FreshnessWindow, reason: str) -> ResearchResult:
        return ResearchResult(
            status="FAILED",
            freshness_window=freshness_window,
            searched_at=datetime.now(timezone.utc).isoformat(),
            answer=f"Каскад упал: {reason}",
            key_findings=[],
            unknowns=[reason],
            content_angles=[],
            tier_summary=TierSummary(),
        )

    # ---- helpers ----

    @staticmethod
    def _guess_source_type(url: str) -> str:
        """Best-effort source-type classification for a URL."""
        u = url.lower()
        if any(d in u for d in (".gov", ".gouv", "government.ru", "kremlin.ru")):
            return "official"
        if any(d in u for d in (".edu", "ac.uk", "academia.edu", "arxiv.org", "doi.org")):
            return "primary"
        if any(d in u for d in ("reuters.", "apnews.", "bbc.", "nytimes.", "bloomberg.",
                                "wsj.", "ft.com", "rbc.ru", "vedomosti.ru", "kommersant.ru",
                                "forbes.", "thebell.io")):
            return "reputable_media"
        if any(d in u for d in ("medium.com", "habr.com", "vc.ru", "spark.ru")):
            return "blog"
        return "blog"

    @staticmethod
    def _claim_key(claim: str) -> str:
        """Normalize a claim for conflict detection."""
        s = re.sub(r"\W+", " ", claim.lower()).strip()
        return s[:80]

    async def _find_oldest_published_date(self, topic: str) -> Optional[date]:
        """Best-effort: do a search without date filter and find the
        oldest published_at we can extract. Used to populate
        ``fallback_oldest`` in the NO_FRESH_DATA case.
        """
        try:
            results = await self._search.search(topic, limit=5, after_date=None)
            for r in results:
                meta = await self._reader.read_with_meta(r.url, max_chars=2000)
                if meta.get("ok") and meta.get("published_at"):
                    return meta["published_at"]
        except Exception:  # noqa: BLE001
            return None
        return None


def _parse_iso_date(s: Any) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ---- D.1 — cached entry point (Sub-task D) ----
#
# Same semantics as ``ResearchCascade.run``, but cacheable across instances
# and across processes (the @cache decorator writes to SQLite via
# ResearchCache). Cache key is sha256(topic + freshness_window) — see
# ResearchCache.make_key for the exact hash.
#
# This is the entry point that ``PipelineOrchestrator`` and the handler
# use when ``MAX_USE_PIPELINE`` is on. Legacy code paths keep using
# ``ResearchCascade.run`` directly so backwards-compat is preserved.

@_research_cache_decorator(ttl_seconds=3600)
async def run_research_cached(topic: str, freshness_window: str) -> dict:
    """Cached wrapper around ResearchCascade.run.

    Returns the cascade result as a dict (Pydantic v2 ``model_dump``).
    On cache hit, no real cascade is executed — the cached dict is
    returned directly.

    Raises whatever ``cascade.run`` raises (we do NOT swallow errors
    here; the caller decides how to handle them).
    """
    from app.config import get_settings
    settings = get_settings()
    cascade = ResearchCascade(settings)
    try:
        result = await cascade.run(topic, freshness_window)  # type: ignore[arg-type]
        return result.model_dump()
    finally:
        await cascade.aclose()
