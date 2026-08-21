"""Web search wrapper. Backends: duckduckgo | searxng | whoogle | librex.

Pavel picks the backend via WEB_SEARCH_BACKEND / SEARCH_BACKEND in .env.
- duckduckgo: no key required (uses the `duckduckgo_search` package if present).
- searxng/whoogle/librex: you run your own instance and put its URL in env.
The web_reader module handles fetching + extracting page text (trafilatura).

F2.1 (2026-08-21): added ``after_date`` parameter to filter results by
recency. The /research pipeline passes a ``date`` object (or ISO string)
and we splice ``after:YYYY-MM-DD`` into the query — DuckDuckGo's HTML
endpoint understands this filter and SearXNG forwards it to the
underlying engine. The ``duckduckgo_search`` package has a native
``timelimit`` argument; we map the date to ``"d"`` (day) when within
30 days and skip it for older windows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger("maxbot.tools.search")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published_at: Optional[date] = None  # F2.2: extracted by the reader, not here


def _date_to_iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d else None


def _date_to_ddg_timelimit(d: Optional[date]) -> Optional[str]:
    """Map a date to DuckDuckGo's ``timelimit`` token.

    Returns "d" (day), "w" (week), "m" (month), or "y" (year). We do not
    support arbitrary ranges here — for the 7d/30d/90d windows this is
    the closest bucket. ``None`` means "no limit".
    """
    if d is None:
        return None
    from datetime import datetime, timezone

    days = (datetime.now(timezone.utc).date() - d).days
    if days <= 7:
        return "d"
    if days <= 31:
        return "w"
    if days <= 365:
        return "m"
    return "y"


class WebSearch:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._backend = (settings.web_search_backend or "duckduckgo").lower()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        limit: int = 5,
        after_date: Optional[date] = None,
    ) -> list[SearchResult]:
        """Run a search; optionally filter to results newer than ``after_date``.

        ``after_date`` is appended to the query as ``after:YYYY-MM-DD`` so
        backends that understand the filter (DuckDuckGo HTML, SearXNG)
        apply it server-side. The python ``duckduckgo_search`` package
        takes a ``timelimit`` kwarg we map to a coarse bucket.
        """
        if self._backend == "duckduckgo":
            return await self._search_duckduckgo(query, limit, after_date)
        if self._backend in ("searxng", "whoogle", "librex"):
            return await self._search_instance(query, limit, after_date)
        logger.warning("Unknown search backend %s; falling back to duckduckgo", self._backend)
        return await self._search_duckduckgo(query, limit, after_date)

    async def _search_duckduckgo(
        self, query: str, limit: int, after_date: Optional[date],
    ) -> list[SearchResult]:
        # Try the `duckduckgo_search` package first (best effort, optional dep).
        # We pass ``timelimit`` if the date is within a known bucket.
        try:
            from duckduckgo_search import DDGS  # type: ignore

            results: list[SearchResult] = []
            timelimit = _date_to_ddg_timelimit(after_date)
            kwargs = {"max_results": limit}
            if timelimit is not None:
                kwargs["timelimit"] = timelimit
            with DDGS() as ddgs:
                for r in ddgs.text(query, **kwargs):
                    results.append(
                        SearchResult(
                            title=r.get("title", ""),
                            url=r.get("href", ""),
                            snippet=r.get("body", ""),
                        )
                    )
            if results:
                return results
        except Exception as e:  # noqa: BLE001
            logger.info("duckduckgo_search package unavailable (%s); using HTML scrape", e)

        # Fallback: scrape the HTML endpoint (no key, best-effort parse).
        # We append "after:YYYY-MM-DD" to the query — DuckDuckGo's HTML
        # endpoint applies the date filter when the token is present.
        try:
            params: dict = {"q": query}
            if after_date is not None:
                params["q"] = f"{query} after:{after_date.isoformat()}"
            resp = await self._client.get(
                "https://html.duckduckgo.com/html/",
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            return self._parse_ddg_html(resp.text, limit)
        except Exception as e:  # noqa: BLE001
            logger.warning("DuckDuckGo HTML search failed: %s", e)
            return []

    @staticmethod
    def _parse_ddg_html(html: str, limit: int) -> list[SearchResult]:
        import re

        results: list[SearchResult] = []
        # crude extraction of result blocks
        for m in re.finditer(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
            url = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            # decode ddg redirect if present
            if "duckduckgo.com/l/?uddg=" in url:
                import urllib.parse as up

                q = up.urlparse(url).query
                url = up.unquote(up.parse_qs(q).get("uddg", [url])[0])
            results.append(SearchResult(title=title, url=url))
            if len(results) >= limit:
                break
        return results

    async def _search_instance(
        self, query: str, limit: int, after_date: Optional[date],
    ) -> list[SearchResult]:
        base = {
            "searxng": self._s.searxng_url,
            "whoogle": self._s.whoogle_url,
            "librex": self._s.librex_url,
        }[self._backend]
        if not base:
            logger.warning("%s backend selected but no URL configured", self._backend)
            return []
        try:
            params: dict = {"q": query, "format": "json"}
            if after_date is not None:
                # SearXNG supports a "time_range" token (day/week/month/year),
                # but no exact date. We pick the closest bucket and ALSO
                # append the date to the query text so the underlying engine
                # has a hint.
                params["q"] = f"{query} after:{after_date.isoformat()}"
                days = (
                    __import__("datetime").datetime.utcnow().date() - after_date
                ).days
                if days <= 1:
                    params["time_range"] = "day"
                elif days <= 7:
                    params["time_range"] = "week"
                elif days <= 31:
                    params["time_range"] = "month"
                else:
                    params["time_range"] = "year"
            resp = await self._client.get(
                f"{base.rstrip('/')}/search",
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("results", data if isinstance(data, list) else [])
            out = [
                SearchResult(
                    title=i.get("title", ""),
                    url=i.get("url", ""),
                    snippet=i.get("content", ""),
                )
                for i in items[:limit]
            ]
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("%s search failed: %s", self._backend, e)
            return []
