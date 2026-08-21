"""Web reader: fetch a URL and extract clean article text via trafilatura.

Falls back to httpx + a basic strip if trafilatura isn't installed.

F2.2 (2026-08-21): added ``read_with_meta`` which returns a small dict
including ``published_at`` (ISO date) and ``title`` extracted from the
HTML. We look for the standard meta tags and a JSON-LD ``datePublished``
field — neither is mandatory, both are best-effort. ``read()`` keeps
its old return-type (str) for backwards compat.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger("maxbot.tools.reader")


_DATE_PATTERNS = [
    # ISO 8601 date
    r"\b(20\d{2})-(\d{2})-(\d{2})\b",
    # Russian "12 января 2026" — we don't try to be clever here, only ISO.
]

_META_DATE_RE = re.compile(
    r"""<meta[^>]+(?:property|name)\s*=\s*["'](?:article:published_time|
                                            og:published_time|
                                            pubdate|
                                            date|
                                            DC\.date\.published|
                                            DC\.date\.created|
                                            DC\.date\.issued|
                                            datePublished)["'][^>]*content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE | re.VERBOSE,
)

_LDJSON_DATE_RE = re.compile(
    r""""datePublished"\s*:\s*"([^"]+)"|'datePublished'\s*:\s*'([^']+)'""",
    re.IGNORECASE,
)

_TITLE_RE = re.compile(
    r"""<meta[^>]+(?:property|name)\s*=\s*["'](?:og:title)["'][^>]*content\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


def _parse_date(s: str) -> Optional[date]:
    """Best-effort parse of an ISO-like date string from HTML metadata."""
    s = s.strip()
    # Pure date: 2024-01-15
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    # Datetime: 2024-01-15T12:30:00Z or +00:00
    try:
        # Normalize trailing Z
        s_norm = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s_norm).date()
    except ValueError:
        return None


class WebReader:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MaxAIBot/1.0)"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def read(self, url: str, max_chars: int = 8000) -> str:
        """Backwards-compatible text-only reader. Used by older call sites."""
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch %s: %s", url, e)
            return ""

        text = self._extract(html)
        return text[:max_chars]

    async def read_with_meta(
        self, url: str, max_chars: int = 8000,
    ) -> dict:
        """F2.2: fetch a URL and return text + extracted metadata.

        Returns a dict with keys:
          * ``text``         — the extracted article text (truncated to max_chars)
          * ``title``        — best-effort <title> / og:title
          * ``published_at`` — best-effort ISO date or None
          * ``url``          — echo of the input URL
          * ``ok``           — True iff the fetch succeeded
        On any HTTP / parse error, ``ok=False`` and ``text=""``.
        """
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch %s: %s", url, e)
            return {"text": "", "title": "", "published_at": None, "url": url, "ok": False}

        text = self._extract(html)[:max_chars]
        title = self._extract_title(html)
        published_at = self._extract_published_at(html)
        return {
            "text": text,
            "title": title,
            "published_at": published_at,
            "url": url,
            "ok": True,
        }

    @staticmethod
    def _extract(html: str) -> str:
        try:
            import trafilatura  # type: ignore

            extracted = trafilatura.extract(html, include_comments=False)
            if extracted:
                return extracted
        except Exception as e:  # noqa: BLE001
            logger.info("trafilatura unavailable (%s); basic strip", e)
        # Basic fallback: strip tags.
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _extract_published_at(html: str) -> Optional[date]:
        m = _META_DATE_RE.search(html)
        if m:
            d = _parse_date(m.group(1))
            if d is not None:
                return d
        m = _LDJSON_DATE_RE.search(html)
        if m:
            d = _parse_date(m.group(1) or m.group(2))
            if d is not None:
                return d
        return None

    @staticmethod
    def _extract_title(html: str) -> str:
        m = _TITLE_RE.search(html)
        if m:
            return m.group(1).strip()
        # Fallback to <title>
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return ""
