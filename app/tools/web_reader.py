"""Web reader: fetch a URL and extract clean article text via trafilatura.

Falls back to httpx + a basic strip if trafilatura isn't installed.
"""
from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger("maxbot.tools.reader")


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
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch %s: %s", url, e)
            return ""

        text = self._extract(html)
        return text[:max_chars]

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
        import re

        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
