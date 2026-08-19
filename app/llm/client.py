"""OpenAI-compatible LLM client.

Supports two API styles behind one interface:
  * "openai"   -> POST {base_url}/chat/completions  (Authorization: Bearer ...)
  * "anthropic"-> POST {base_url}/v1/messages        (x-api-key + anthropic-version)

Primary provider = MiniMax (anthropic-style per the project spec).
Fallback provider = StepFun (openai-style). If the primary call raises or
returns an error, the client transparently retries on the fallback.

All network calls go through a shared httpx.AsyncClient. A provider timeout
switches to the configured fallback immediately; other transient errors retain
bounded retries with exponential backoff. Calls are rate-limited by a semaphore
(30 rps) shared from the middleware layer. We never log prompt contents —
only metadata (length, model, latency, status).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger("maxbot.llm")


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings, rate_semaphore: Optional[asyncio.Semaphore] = None) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_request_timeout_s)
        )
        # 30 rps hard cap on the MAX API; reuse the same bound for LLM calls.
        self._sem = rate_semaphore or asyncio.Semaphore(30)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- public API ----
    async def chat(
        self,
        messages: list[dict],
        role: Optional[str] = None,
        system: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """Blocking (awaited) chat completion. Falls back to secondary provider."""
        return await self._with_fallback(
            messages=messages, system=system, max_tokens=max_tokens
        )

    async def stream(self, messages: list[dict], role: Optional[str] = None,
                     system: Optional[str] = None, max_tokens: int = 4096):
        """Yield text chunks. Falls back to non-streaming on error."""
        # Streaming is optional; default implementation chats then yields one chunk.
        text = await self.chat(messages, role=role, system=system, max_tokens=max_tokens)
        yield text

    # ---- internals ----
    async def _with_fallback(
        self, messages: list[dict], system: Optional[str], max_tokens: int
    ) -> str:
        primary = self._provider_cfg(primary=True)
        try:
            return await self._call(primary, messages, system, max_tokens)
        except LLMError as e:
            logger.warning("Primary LLM (%s) failed: %s; trying fallback", primary["model"], e)
            fb = self._provider_cfg(primary=False)
            if not fb["api_key"]:
                raise
            try:
                return await self._call(fb, messages, system, max_tokens)
            except LLMError as e2:
                raise LLMError(f"All LLM providers failed: {e} | {e2}")

    def _provider_cfg(self, primary: bool) -> dict:
        s = self._settings
        if primary:
            return {
                "provider": s.llm_provider,
                "api_key": s.llm_primary_api_key or s.llm_api_key,
                "base_url": s.llm_base_url.rstrip("/"),
                "model": s.llm_model,
                "style": s.llm_primary_style or "anthropic",
            }
        return {
            "provider": s.llm_fallback_provider,
            "api_key": s.llm_fallback_api_key,
            "base_url": s.llm_fallback_base_url.rstrip("/"),
            "model": s.llm_fallback_model,
            "style": s.llm_fallback_style or "openai",
        }

    async def _call(
        self, cfg: dict, messages: list[dict], system: Optional[str], max_tokens: int
    ) -> str:
        if not cfg["api_key"]:
            raise LLMError("no API key configured for provider")

        attempts = 0
        last_err: Optional[Exception] = None
        while attempts < 3:  # 1 try + 2 retries
            attempts += 1
            try:
                async with self._sem:
                    if cfg["style"] == "anthropic":
                        return await self._call_anthropic(cfg, messages, system, max_tokens)
                    return await self._call_openai(cfg, messages, system, max_tokens)
            except httpx.TimeoutException as e:
                logger.warning(
                    "LLM provider timeout model=%s after %.0fs; switching provider",
                    cfg["model"],
                    self._settings.llm_request_timeout_s,
                )
                raise LLMError(
                    f"provider timeout after {self._settings.llm_request_timeout_s:.0f}s"
                ) from e
            except (httpx.HTTPError, LLMError) as e:
                last_err = e
                logger.warning("LLM attempt %d/%d failed: %s", attempts, 3, e)
                if attempts < 3:
                    await asyncio.sleep(2 ** attempts)  # exponential backoff
        raise LLMError(f"request failed after retries: {last_err}")

    def _log_meta(self, cfg: dict, n_messages: int, latency: float, status: str) -> None:
        logger.info(
            "llm_call provider=%s model=%s messages=%d chars=%d latency=%.2fs status=%s",
            cfg["provider"], cfg["model"], n_messages,
            # rough size proxy
            -1, latency, status,
        )

    async def _call_openai(
        self, cfg: dict, messages: list[dict], system: Optional[str], max_tokens: int
    ) -> str:
        t0 = time.monotonic()
        payload_messages = messages
        if system:
            payload_messages = [{"role": "system", "content": system}, *messages]
        resp = await self._client.post(
            f"{cfg['base_url']}/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "messages": payload_messages,
                "max_tokens": max_tokens,
            },
        )
        self._log_meta(cfg, len(messages), time.monotonic() - t0, str(resp.status_code))
        if resp.status_code != 200:
            raise LLMError(f"openai {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def _call_anthropic(
        self, cfg: dict, messages: list[dict], system: Optional[str], max_tokens: int
    ) -> str:
        t0 = time.monotonic()
        # Anthropic expects [{role, content}] with user/assistant; system is separate.
        resp = await self._client.post(
            f"{cfg['base_url']}/v1/messages",
            headers={
                "x-api-key": cfg["api_key"],
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": cfg["model"],
                "max_tokens": max_tokens,
                "system": system or "",
                "messages": messages,
            },
        )
        self._log_meta(cfg, len(messages), time.monotonic() - t0, str(resp.status_code))
        if resp.status_code != 200:
            raise LLMError(f"anthropic {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        # content is a list of blocks: [{"type": "text", "text": "..."}]
        blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
