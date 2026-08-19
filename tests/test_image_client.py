"""Tests for ImageClient (MiniMax image_generation) — no real network.

We use httpx's MockTransport to feed canned responses, so the whole retry /
download / error-mapping pipeline is exercised without touching the internet.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.llm.image_client import ImageClient, ImageGenError, user_message_for


def _settings(**over) -> Settings:
    s = Settings(_env_file=None)
    s.llm_api_key = ""
    s.llm_primary_api_key = "test-key-abc"
    s.image_max_retries = 2
    for k, v in over.items():
        setattr(s, k, v)
    return s


def _make_client(handler, *, settings=None) -> ImageClient:
    s = settings or _settings()
    transport = httpx.MockTransport(handler)
    client = ImageClient(s)
    fake_async = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )
    client._client = fake_async  # type: ignore[attr-defined]
    return client


def _api_ok_handler(body_dict):
    """Return a handler that:
      * returns the given JSON for POST /v1/image_generation,
      * returns a fake PNG body for any other URL.
    """
    def _h(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            return httpx.Response(200, json=body_dict)
        return httpx.Response(200, content=b"\x89PNG_FAKE_BYTES")
    return _h


# --- happy path: text-to-image ---


@pytest.mark.asyncio
async def test_generate_t2i_happy_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            captured["auth"] = request.headers.get("Authorization")
            try:
                captured["body"] = json.loads(request.content or b"{}")
            except Exception:
                captured["body"] = None
            return httpx.Response(
                200,
                json={
                    "id": "req-1",
                    "data": {"image_urls": ["https://cdn.example.com/img.png"]},
                    "metadata": {"success_count": "1", "failed_count": "0"},
                },
            )
        return httpx.Response(200, content=b"\x89PNG_FAKE_BYTES")

    client = _make_client(handler)
    png_bytes = await client.generate("a cat in the window", aspect_ratio="1:1")
    assert png_bytes == b"\x89PNG_FAKE_BYTES"
    assert captured["auth"] == "Bearer test-key-abc"
    body = captured["body"]
    assert body["model"] == "image-01"
    assert body["prompt"] == "a cat in the window"
    assert body["aspect_ratio"] == "1:1"
    assert body["n"] == 1
    assert body["response_format"] == "url"
    assert body["prompt_optimizer"] is True
    assert "subject_reference" not in body


# --- image-to-image ---


@pytest.mark.asyncio
async def test_generate_i2i_adds_subject_reference():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            try:
                captured["body"] = json.loads(request.content or b"{}")
            except Exception:
                captured["body"] = None
            return httpx.Response(
                200,
                json={"data": {"image_urls": ["https://x/y.png"]}},
            )
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    await client.generate(
        "woman in red dress",
        aspect_ratio="4:3",
        subject_image_url="https://example.com/ref.png",
    )
    assert captured["body"]["subject_reference"] == [
        {"type": "character", "image_file": "https://example.com/ref.png"}
    ]


# --- prompt truncation ---


@pytest.mark.asyncio
async def test_long_prompt_is_truncated():
    s = _settings(image_prompt_max_chars=20)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            try:
                captured["body"] = json.loads(request.content or b"{}")
            except Exception:
                captured["body"] = None
            return httpx.Response(
                200,
                json={"data": {"image_urls": ["https://x/y.png"]}},
            )
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler, settings=s)
    await client.generate("a " * 100)  # 200 chars
    assert len(captured["body"]["prompt"]) == 20


# --- aspect ratio fallback ---


@pytest.mark.asyncio
async def test_invalid_aspect_falls_back_to_1_1():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            try:
                captured["body"] = json.loads(request.content or b"{}")
            except Exception:
                captured["body"] = None
            return httpx.Response(
                200,
                json={"data": {"image_urls": ["https://x/y.png"]}},
            )
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    await client.generate("hello", aspect_ratio="99:99")
    assert captured["body"]["aspect_ratio"] == "1:1"


@pytest.mark.asyncio
async def test_undocumented_5_4_aspect_falls_back_to_1_1():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            captured["body"] = json.loads(request.content or b"{}")
            return httpx.Response(200, json={"data": {"image_urls": ["https://x/y.png"]}})
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    await client.generate("hello", aspect_ratio="5:4")
    assert captured["body"]["aspect_ratio"] == "1:1"


@pytest.mark.asyncio
async def test_client_uses_request_timeout_from_settings():
    client = ImageClient(_settings(image_request_timeout_s=7.5))
    http = await client._ensure_client()
    try:
        assert http.timeout.read == 7.5
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_client_context_manager_closes_http_pool():
    client = ImageClient(_settings())
    async with client:
        await client._ensure_client()
        assert client._client is not None
    assert client._client is None


@pytest.mark.asyncio
async def test_response_status_and_request_id_are_logged(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            return httpx.Response(
                200,
                json={"id": "trace-123", "data": {"image_urls": ["https://x/y.png"]}},
            )
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    with caplog.at_level("INFO", logger="maxbot.image"):
        await client.generate("hello")
    assert "status=200" in caplog.text
    assert "request_id=trace-123" in caplog.text


# --- retry counter: rate limit HTTP 429 → retried until success ---


@pytest.mark.asyncio
async def test_rate_limit_429_is_retried_until_success():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                return httpx.Response(
                    429,
                    json={"base_resp": {"status_code": 1002, "status_msg": "rate"}},
                )
            return httpx.Response(
                200,
                json={"data": {"image_urls": ["https://x/y.png"]}},
            )
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    await client.generate("hi")
    # 2 failures + 1 success = 3 attempts.
    assert attempts["n"] == 3


# --- permanent MiniMax errors are not retried ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_status", "api_code"),
    [(401, 1004), (402, 1008), (400, 1026), (400, 2013), (401, 2049)],
)
async def test_permanent_minimax_error_is_not_retried(http_status, api_code):
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            attempts["n"] += 1
            return httpx.Response(
                http_status,
                json={"base_resp": {"status_code": api_code, "status_msg": "permanent"}},
            )
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    with pytest.raises(ImageGenError) as exc:
        await client.generate("hi")
    assert attempts["n"] == 1
    assert exc.value.code == str(api_code)


# --- 1008 balance error → friendly message ---


@pytest.mark.asyncio
async def test_1008_balance_user_friendly():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            return httpx.Response(
                402,
                json={"base_resp": {"status_code": 1008, "status_msg": "balance"}},
            )
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    with pytest.raises(ImageGenError) as exc:
        await client.generate("hi")
    msg = user_message_for(exc.value)
    assert "баланс" in msg.lower()


# --- 1026 content block → friendly message ---


@pytest.mark.asyncio
async def test_1026_content_block_user_friendly():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            return httpx.Response(
                400,
                json={"base_resp": {"status_code": 1026, "status_msg": "blocked"}},
            )
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    with pytest.raises(ImageGenError) as exc:
        await client.generate("forbidden content")
    msg = user_message_for(exc.value)
    assert "🚫" in msg or "контент" in msg.lower()


# --- missing key ---


@pytest.mark.asyncio
async def test_missing_api_key_raises():
    s = _settings()
    s.llm_api_key = ""
    s.llm_primary_api_key = ""
    client = ImageClient(s)
    with pytest.raises(ImageGenError) as exc:
        await client.generate("hi")
    assert exc.value.code == "MISSING_KEY"


# --- empty prompt ---


@pytest.mark.asyncio
async def test_empty_prompt_raises():
    client = _make_client(_api_ok_handler({"data": {"image_urls": ["https://x/y.png"]}}))
    with pytest.raises(ImageGenError) as exc:
        await client.generate("   ")
    assert exc.value.code == "EMPTY_PROMPT"


# --- empty image_urls ---


@pytest.mark.asyncio
async def test_empty_image_urls_raises():
    client = _make_client(_api_ok_handler({"data": {"image_urls": []}}))
    with pytest.raises(ImageGenError) as exc:
        await client.generate("hi")
    assert exc.value.code == "NO_IMAGES"


# --- bad URL ---


@pytest.mark.asyncio
async def test_bad_url_raises():
    client = _make_client(_api_ok_handler({"data": {"image_urls": ["not-a-url"]}}))
    with pytest.raises(ImageGenError) as exc:
        await client.generate("hi")
    assert exc.value.code == "BAD_URL"


# --- 5xx upstream is retried ---


@pytest.mark.asyncio
async def test_5xx_is_retried():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                return httpx.Response(503, json={"base_resp": {"status_code": 9999, "status_msg": "upstream"}})
            return httpx.Response(200, json={"data": {"image_urls": ["https://x/y.png"]}})
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    await client.generate("hi")
    assert attempts["n"] == 3


# --- 4xx non-retryable (NOT in _RATE_LIMIT_CODES, status NOT in _RETRYABLE_HTTP) ---


@pytest.mark.asyncio
async def test_400_bad_request_not_retried():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/v1/image_generation" in str(request.url):
            attempts["n"] += 1
            return httpx.Response(400, json={"base_resp": {"status_code": 9999, "status_msg": "bad"}})
        return httpx.Response(200, content=b"PNG")

    client = _make_client(handler)
    with pytest.raises(ImageGenError) as exc:
        await client.generate("hi")
    # 400 is not in _RETRYABLE_HTTP and 9999 is not in _RATE_LIMIT_CODES → no retry.
    assert attempts["n"] == 1
    assert exc.value.code == "9999"