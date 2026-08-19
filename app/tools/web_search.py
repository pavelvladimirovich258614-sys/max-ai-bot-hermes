"""Web search wrapper. Backends: duckduckgo | searxng | whoogle | librex.

Pavel picks the backend via WEB_SEARCH_BACKEND / SEARCH_BACKEND in .env.
- duckduckgo: no key required (uses the `duckduckgo_search` package if present).
- searxng/whoogle/librex: you run your own instance and put its URL in env.
The web_reader module handles fetching + extracting page text (trafilatura).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger("maxbot.tools.search")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


class WebSearch:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._backend = (settings.web_search_backend or "duckduckgo").lower()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        if self._backend == "duckduckgo":
            return await self._search_duckduckgo(query, limit)
        if self._backend in ("searxng", "whoogle", "librex"):
            return await self._search_instance(query, limit)
        logger.warning("Unknown search backend %s; falling back to duckduckgo", self._backend)
        return await self._search_duckduckgo(query, limit)

    async def _search_duckduckgo(self, query: str, limit: int) -> list[SearchResult]:
        # Try the `duckduckgo_search` package first (best effort, optional dep).
        try:
            from duckduckgo_search import DDGS  # type: ignore

            results: list[SearchResult] = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=limit):
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
        try:
            resp = await self._client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
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

    async def _search_instance(self, query: str, limit: int) -> list[SearchResult]:
        base = {
            "searxng": self._s.searxng_url,
            "whoogle": self._s.whoogle_url,
            "librex": self._s.librex_url,
        }[self._backend]
        if not base:
            logger.warning("%s backend selected but no URL configured", self._backend)
            return []
        try:
            resp = await self._client.get(
                f"{base.rstrip('/')}/search",
                params={"q": query, "format": "json"},
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
