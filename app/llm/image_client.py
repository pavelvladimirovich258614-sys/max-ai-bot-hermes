"""MiniMax image_generation API client.

Sends text-to-image and image-to-image requests to:
    POST https://api.minimax.io/v1/image_generation
with the same bearer token used for the LLM primary (`llm_primary_api_key` /
`llm_api_key` in settings).

Returns raw PNG/JPEG bytes (downloaded immediately — the URLs expire in 24h).
Retries transient failures (1002 rate limit, 5xx) up to `image_max_retries`.

References (verified 2026-08-19):
  * T2I: https://platform.minimax.io/docs/api-reference/image-generation-t2i
  * I2I: https://platform.minimax.io/docs/api-reference/image-generation-i2i
  * Rate limits: 10 RPM for image-01.
  * Errors: 1002 rate, 1004 auth, 1008 balance, 1026 content block,
    2013 invalid params, 2049 invalid key.

The class is a stateless service object — instantiate once and reuse.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("maxbot.image")

# MiniMax public API host. Same vendor as the LLM (api.minimax.io).
DEFAULT_BASE = "https://api.minimax.io"

# MiniMax error codes that are genuinely transient. Authentication, balance,
# content-policy and invalid-parameter failures must not be retried.
_RETRYABLE_CODES = {"1002", "TIMEOUT", "NETWORK"}
_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504, 522, 524}

# Aspect ratios listed by the current image-01 API reference.
_VALID_ASPECTS = {
    "1:1", "4:3", "3:4", "16:9", "9:16", "2:3", "3:2", "21:9",
}


class ImageGenError(Exception):
    """Raised for any non-retryable failure or final retry exhaustion.

    The ``code`` attribute holds the MiniMax error code (string) when known,
    ``status`` the HTTP status, ``detail`` the raw payload — so callers can
    present a user-facing message tailored to the failure mode.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail


class ImageClient:
    """Thin async wrapper around MiniMax image_generation.

    Constructed with a Settings instance (so it picks up the API key from
    ``.env``); reused across requests (httpx connection pool).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str = DEFAULT_BASE,
        timeout_s: float | None = None,
    ) -> None:
        self._settings = settings
        self._base = base_url.rstrip("/")
        self._timeout = (
            settings.image_request_timeout_s if timeout_s is None else timeout_s
        )
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                headers={"User-Agent": "max-ai-bot/image-1.0"},
            )
        return self._client

    async def __aenter__(self) -> "ImageClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ----------------- public API -----------------

    async def generate(
        self,
        prompt: str,
        *,
        aspect_ratio: str | None = None,
        subject_image_url: str | None = None,
        model: str = "image-01",
    ) -> bytes:
        """Generate one image and return its raw bytes.

        Args:
          * ``prompt``: text description, ≤ ``settings.image_prompt_max_chars``
            characters (Pavel: 1500). We truncate, never error.
          * ``aspect_ratio``: one of MiniMax's supported ratios (default
            ``settings.image_aspect_default``).
          * ``subject_image_url``: optional reference image for image-to-image
            (character consistency). Must be a publicly fetchable URL.
          * ``model``: MiniMax model name. Default ``image-01``.

        Raises:
          * ``ImageGenError`` on every non-retryable failure or after retries
            are exhausted. User-facing message lives in ``str(exc)``.
        """
        api_key = self._settings.llm_primary_api_key or self._settings.llm_api_key
        if not api_key:
            raise ImageGenError(
                "Не задан llm_primary_api_key / llm_api_key в .env",
                code="MISSING_KEY",
            )
        prompt = (prompt or "").strip()
        if not prompt:
            raise ImageGenError("Пустой промпт", code="EMPTY_PROMPT")
        max_chars = self._settings.image_prompt_max_chars
        if len(prompt) > max_chars:
            logger.info("image: prompt %d chars → truncated to %d", len(prompt), max_chars)
            prompt = prompt[:max_chars]

        aspect = aspect_ratio or self._settings.image_aspect_default
        if aspect not in _VALID_ASPECTS:
            logger.warning("image: unsupported aspect_ratio %r — falling back to 1:1", aspect)
            aspect = "1:1"

        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect,
            "n": 1,  # Pavel: never more than 1 — regeneration is a new request.
            "response_format": "url",
            "prompt_optimizer": True,
        }
        if subject_image_url:
            body["subject_reference"] = [
                {"type": "character", "image_file": subject_image_url}
            ]

        url = await self._post_with_retry(body, api_key=api_key)
        # Download immediately — URL expires in 24h.
        return await self._download_bytes(url)

    # ----------------- internals -----------------

    async def _post_with_retry(self, body: dict[str, Any], *, api_key: str) -> str:
        max_retries = self._settings.image_max_retries
        last_err: ImageGenError | None = None
        for attempt in range(max_retries + 1):
            try:
                logger.info(
                    "image_generation request attempt=%d/%d model=%s aspect=%s prompt_chars=%d",
                    attempt + 1,
                    max_retries + 1,
                    body.get("model"),
                    body.get("aspect_ratio"),
                    len(str(body.get("prompt") or "")),
                )
                return await self._post_once(body, api_key=api_key)
            except ImageGenError as e:
                last_err = e
                if not _should_retry(e, attempt, max_retries):
                    raise
                backoff = 2 ** attempt  # 1s, 2s, 4s, ...
                logger.warning(
                    "image: retryable error (code=%s status=%s) attempt %d/%d, sleeping %.1fs",
                    e.code, e.status, attempt + 1, max_retries, backoff,
                )
                await asyncio.sleep(backoff)
        # All retries exhausted.
        assert last_err is not None
        raise last_err

    async def _post_once(self, body: dict[str, Any], *, api_key: str) -> str:
        client = await self._ensure_client()
        url = f"{self._base}/v1/image_generation"
        try:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.TimeoutException as e:
            raise ImageGenError(
                "Таймаут при обращении к image_generation",
                code="TIMEOUT", detail=str(e),
            ) from e
        except httpx.HTTPError as e:
            raise ImageGenError(
                f"Сетевая ошибка: {type(e).__name__}",
                code="NETWORK", detail=str(e),
            ) from e

        # Parse JSON body for both success and error paths.
        try:
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            raise ImageGenError(
                f"image_generation вернул не-JSON (status={resp.status_code})",
                status=resp.status_code, code="BAD_JSON", detail=str(e),
            ) from e

        request_id = payload.get("id") if isinstance(payload, dict) else None
        logger.info(
            "image_generation response status=%s request_id=%s body=%s",
            resp.status_code,
            request_id or "-",
            _safe_payload_excerpt(payload),
        )

        if resp.status_code >= 400:
            code, msg = _extract_minimax_error(payload)
            raise ImageGenError(
                msg or f"image_generation HTTP {resp.status_code}",
                status=resp.status_code, code=code, detail=payload,
            )

        # Response shape (verified 2026-08-19):
        #   {"id": "...", "data": {"image_urls": ["https://...png"]},
        #    "metadata": {"failed_count": "0", "success_count": "1"}}
        data = payload.get("data") if isinstance(payload, dict) else None
        image_urls = (data or {}).get("image_urls") or []
        if not image_urls:
            raise ImageGenError(
                "image_generation вернул пустой image_urls",
                status=resp.status_code, code="NO_IMAGES", detail=payload,
            )
        first = image_urls[0]
        if not isinstance(first, str) or not first.startswith("http"):
            raise ImageGenError(
                "image_generation вернул битый URL",
                status=resp.status_code, code="BAD_URL", detail=first,
            )
        return first

    async def _download_bytes(self, url: str) -> bytes:
        # B4 (2026-08-19): use settings.image_download_timeout_s instead of hardcoded 60.0.
        dl_timeout = self._settings.image_download_timeout_s
        client = await self._ensure_client()
        try:
            resp = await client.get(url, timeout=httpx.Timeout(dl_timeout, connect=10.0))
        except httpx.TimeoutException as e:
            raise ImageGenError(
                f"Таймаут скачивания картинки ({dl_timeout:.0f}с)",
                code="DOWNLOAD_TIMEOUT", detail=str(e),
            ) from e
        except httpx.HTTPError as e:
            raise ImageGenError(
                f"Ошибка скачивания: {type(e).__name__}",
                code="DOWNLOAD_NETWORK", detail=str(e),
            ) from e
        if resp.status_code >= 400:
            raise ImageGenError(
                f"Скачивание провалилось HTTP {resp.status_code}",
                status=resp.status_code, code="DOWNLOAD_HTTP",
                detail=resp.text[:200],
            )
        if not resp.content:
            raise ImageGenError("Скачанный файл пустой", code="EMPTY_BODY")
        return resp.content


def _safe_payload_excerpt(payload: Any, limit: int = 500) -> str:
    """Compact response diagnostics without secrets or expiring image URLs."""
    if isinstance(payload, dict):
        safe = dict(payload)
        data = safe.get("data")
        if isinstance(data, dict) and data.get("image_urls"):
            safe["data"] = {**data, "image_urls": ["<redacted-url>"]}
        text = repr(safe)
    else:
        text = repr(payload)
    return text[:limit]


def _should_retry(err: ImageGenError, attempt: int, max_retries: int) -> bool:
    """Retry only transient network, rate-limit and upstream failures."""
    if attempt >= max_retries:
        return False
    if err.code in _RETRYABLE_CODES:
        return True
    if err.status is not None and err.status in _RETRYABLE_HTTP:
        return True
    return False


def _extract_minimax_error(payload: Any) -> tuple[str | None, str | None]:
    """Pull `(code, message)` out of MiniMax's error envelope.

    Shape (verified 2026-08-19):
      {"base_resp": {"status_code": 1002, "status_msg": "rate limit exceeded"}}
    or
      {"error": {"code": "1002", "message": "..."}}
    """
    if not isinstance(payload, dict):
        return None, None
    base = payload.get("base_resp") or {}
    code = base.get("status_code")
    msg = base.get("status_msg")
    if code is not None or msg is not None:
        return (str(code) if code is not None else None), msg
    err = payload.get("error") or {}
    code = err.get("code") or err.get("status_code")
    msg = err.get("message") or err.get("status_msg")
    if code is not None or msg is not None:
        return (str(code) if code is not None else None), msg
    return None, None


# ---- friendly user-facing messages by error code ----

_USER_MESSAGES: dict[str, str] = {
    "1002":   "⏱ Превышен лимит запросов к image-01 (10 RPM). Подожди минуту и попробуй ещё раз.",
    "1004":   "🔑 Невалидный API-ключ MiniMax. Проверь llm_primary_api_key в .env.",
    "1008":   "💰 Недостаточно баланса MiniMax. Пополни аккаунт.",
    "1026":   "🚫 Промпт отклонён фильтром контента. Перефразируй запрос.",
    "2013":   "⚠️ Невалидные параметры запроса к image_generation.",
    "2049":   "🔑 Невалидный API-ключ MiniMax. Проверь llm_primary_api_key в .env.",
    "MISSING_KEY": "🔑 Не задан llm_primary_api_key в .env.",
    "EMPTY_PROMPT": "📝 Промпт пустой — пришли текст для генерации.",
    "TIMEOUT":   "⏱ MiniMax не ответил вовремя. Попробуй ещё раз.",
    "NETWORK":   "🌐 Сетевая ошибка при обращении к MiniMax.",
    "BAD_JSON":  "⚠️ MiniMax вернул неожиданный ответ. Попробуй ещё раз.",
    "NO_IMAGES": "⚠️ MiniMax не вернул картинку. Попробуй переформулировать промпт.",
    "BAD_URL":   "⚠️ MiniMax вернул битый URL картинки.",
    "DOWNLOAD_TIMEOUT": "⏱ Таймаут скачивания картинки. Попробуй ещё раз.",
    "DOWNLOAD_NETWORK": "🌐 Не удалось скачать картинку.",
    "DOWNLOAD_HTTP":    "⚠️ Скачивание картинки провалилось.",
    "EMPTY_BODY":       "⚠️ MiniMax вернул пустой файл.",
}


def user_message_for(err: ImageGenError) -> str:
    """Translate an ImageGenError into a friendly Russian message for the user."""
    if err.code and err.code in _USER_MESSAGES:
        return _USER_MESSAGES[err.code]
    if err.status is not None:
        return f"⚠️ Image generation failed (HTTP {err.status}). Попробуй ещё раз."
    return f"⚠️ Не удалось сгенерировать картинку: {err}"