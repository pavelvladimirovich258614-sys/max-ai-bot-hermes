# MiniMax `image_generation` API research

Research checked on 2026-08-19. This report documents MiniMax's dedicated,
native image endpoint (not its OpenAI-compatible `/v1/images/generations`
route). The official global URL is:

```text
POST https://api.minimax.io/v1/image_generation
```

The same path exists on the China host; OpenClaw documents
`MINIMAX_API_HOST=https://api.minimaxi.com` as the way to select that regional
base. The current native endpoint is therefore `.../v1/image_generation`, not
`.../v1/images/generations`.[1][2][9]

## Endpoint + auth

- **Method/path:** `POST https://api.minimax.io/v1/image_generation`.[1][2]
- **Authentication:** HTTP Bearer authentication in the `Authorization` header:
  `Authorization: Bearer <MINIMAX_API_KEY>`. No API key is sent in the JSON
  body.[1][2]
- **Content type:** `Content-Type: application/json`.[1][2]
- **Credential scope:** a pay-as-you-go Open Platform API key is the standard
  credential for this endpoint. MiniMax's overview separately describes a
  Token Plan Subscription Key; do not assume that a Token Plan key and a
  standard pay-as-you-go API key are interchangeable. The overview says
  Subscription Keys are separate from pay-as-you-go API Keys.[6]
- **Base URL detail:** the image endpoint ignores the chat/Anthropic-compatible
  path suffix. Use `https://api.minimax.io` as the host and append the fixed
  `/v1/image_generation` path; a report that `api.minimax.chat/v1` returned
  2049 while the same key worked at `api.minimax.io` confirms the host/path
  distinction.[9][11]
- **Important runtime behavior:** this endpoint can return HTTP 200 with an
  application-level `base_resp.status_code` failure. Code handling must inspect
  `base_resp`, not only `response.status_code`.[1][2][13]

The official image reference uses the exact request headers shown below.[1]

```http
Authorization: Bearer <MINIMAX_API_KEY>
Content-Type: application/json
```

## Request body schema

The OpenAPI schema is authoritative and is linked from both the text-to-image
and image-to-image reference pages.[7][8]

### Common fields

| Field | Type | Required | Description/default |
|---|---|---:|---|
| `model` | string | yes | `image-01` for the text-to-image operation; the image-to-image OpenAPI enum currently lists `image-01` and `image-01-live`.[1][2][7][8] |
| `prompt` | string | yes | Image description; maximum **1500 characters**.[1][2][7][8] |
| `aspect_ratio` | enum | no | Default `1:1`; see supported values below.[1][2] |
| `width` | integer | no | Only effective for `image-01`; must be paired with `height`; 512–2048 and divisible by 8. If both dimensions and `aspect_ratio` are sent, `aspect_ratio` wins.[1][2] |
| `height` | integer | no | Same rules as `width`.[1][2] |
| `response_format` | enum | no | `url` (default) or `base64`. URL output expires after 24 hours.[1][2][7][8] |
| `seed` | integer | no | Reproducibility seed. If omitted, generated images use random seeds (each image gets a unique random seed in the current image-to-image description).[1][2] |
| `n` | integer | no | Number of images; default 1, valid range **1–9**.[1][2][7][8] |
| `prompt_optimizer` | boolean | no | Automatic prompt optimization; default `false`.[1][2] |
| `subject_reference` | array of objects | no | Reference image(s) for image-to-image; the guide says only one reference image per request. Each object currently supports `type: "character"` and `image_file` as a public URL or Base64 data URL.[2][3][8] |

### Text-to-image example

This is the full documented request shape, with the model and fields used by
the official example.[1][7]

```json
{
  "model": "image-01",
  "prompt": "A man in a white t-shirt, full-body, standing front view, outdoors, with the Venice Beach sign in the background, Los Angeles. Fashion photography in 90s documentary style, film grain, photorealistic.",
  "aspect_ratio": "16:9",
  "response_format": "url",
  "n": 3,
  "prompt_optimizer": true
}
```

### Image-to-image / character consistency example

The official image-to-image request uses one `subject_reference` object with
`type: "character"` and `image_file` set to a public URL. The schema says the
reference can alternatively be a Base64 data URL; it lists JPG/JPEG/PNG and
less than 10 MB. The image guide says only one reference image is supported
per request.[2][3][8]

```json
{
  "model": "image-01",
  "prompt": "A girl looking into the distance from a library window",
  "aspect_ratio": "16:9",
  "subject_reference": [
    {
      "type": "character",
      "image_file": "https://cdn.hailuoai.com/prod/2025-08-12-17/video_cover/1754990600020238321-411603868533342214-cover.jpg"
    }
  ],
  "response_format": "url",
  "n": 2,
  "prompt_optimizer": true
}
```

The official guide demonstrates the same shape with `response_format` set to
`base64` and reads `data.image_base64`; the URL mode is shown below because the
function in the final section returns image bytes and intentionally downloads the
first generated URL immediately.[1][2][3]

## Response shape

The MiniMax-native response is **not** OpenAI's `data[].url` shape. With
`response_format: "url"`, the exact output field is under `data.image_urls` (a
string array), and `data.image_base64` is used for `base64` output.[1][2][7][8]

```json
{
  "id": "03ff3cd0820949eb8a410056b5f21d38",
  "data": {
    "image_urls": [
      "https://example.invalid/generated-image-1.jpeg",
      "https://example.invalid/generated-image-2.jpeg",
      "https://example.invalid/generated-image-3.jpeg"
    ]
  },
  "metadata": {
    "failed_count": 0,
    "success_count": 3
  },
  "base_resp": {
    "status_code": 0,
    "status_msg": "success"
  }
}
```

The real URL strings above are placeholders; the official example substitutes
`XXX`. The full field contract is:

- top-level `id`: request trace ID;
- top-level `data`: object, not an array;
- `data.image_urls`: array of URL strings when URL format is requested;
- `data.image_base64`: array of Base64 strings when Base64 format is requested;
- top-level `metadata.failed_count` and `metadata.success_count`;
- top-level `base_resp.status_code` and `base_resp.status_msg`.[1][2][7][8]

The code below therefore reads `data.image_urls[0]`, downloads it, and returns
its bytes. This avoids depending on the OpenAI-compatible `data[].url` convention.

## Errors table

The official error reference lists `1002`, `1004`, `1008`, `1026`, and `2049`.
The native image OpenAPI additionally repeats several of them in `BaseResp`.
Important caveat: the endpoint's documented success response is HTTP 200, and
A local unauthenticated probe against the documented URL returned **HTTP 200** with
`base_resp.status_code: 1004`; therefore a table that claims a fixed HTTP
status for every code is inaccurate. The application-level body remains the
authoritative signal.[5][7][8][13]

| Code | Meaning | HTTP status to expect in the native image endpoint | Body shape | Retryable? | User-facing message |
|---:|---|---|---|---|---|
| `1002` | Rate limit; try again later.[5] | **No fixed status documented for the native image endpoint.** Many gateways conventionally use HTTP 429, but treat the JSON code as authoritative. | `{"base_resp":{"status_code":1002,"status_msg":"rate limit"}}` (the exact `status_msg` may vary; inspect `base_resp`).[5][7][8] | Yes, with backoff; respect the 10 RPM limit and do not spin aggressively.[4][7] | “The image service is busy. Please try again shortly.” |
| `1004` | Not authorized, token/group mismatch, or cookie missing; verify the API key is correct and active.[5] | **No fixed status documented for the native image endpoint.** A real unauthenticated call to the official endpoint returned HTTP 200 with this code; other API surfaces may return 401. | `{"base_resp":{"status_code":1004,"status_msg":"login fail: Please carry the API secret key in the 'Authorization' field of the request header"}}`.[5][7][8][13] | No; fix the key/header/host first. | “The MiniMax API key was rejected. Check `MINIMAX_API_KEY` and `Authorization: Bearer …`.” |
| `1008` | Insufficient account balance.[5] | **No fixed status documented for the native image endpoint.** A different MiniMax API surface documents HTTP 402 for this semantic error, but do not rely on that for the image endpoint. | `{"base_resp":{"status_code":1008,"status_msg":"insufficient balance"}}` (message may vary).[5][7][8] | No; add balance or usable quota. | “Your MiniMax image quota/balance is insufficient.” |
| `1026` | Input content is sensitive; change the input.[5] | **No fixed status documented for the native image endpoint.** A different MiniMax API surface documents HTTP 422; inspect the image response body. | `{"base_resp":{"status_code":1026,"status_msg":"input new_sensitive"}}`; the image OpenAPI calls this “Sensitive content detected in prompt.”[5][7][8] | No, do not retry unchanged; ask for revised input. | “The request was blocked by MiniMax's content safety filter. Please revise the prompt/reference.” |
| `2049` | Invalid API key.[5] | **No fixed status documented for the native image endpoint.** A compatibility-layer report showed HTTP 401 with code 2049, but the image endpoint's own probe demonstrates that HTTP 200 can carry a body-level failure. | `{"base_resp":{"status_code":2049,"status_msg":"invalid api key"}}`; the exact text may be uppercase “Invalid API Key”.[5][7][8][11][13] | No; verify the key is active and is the correct credential type. | “The MiniMax API key is invalid. Check the key and account/key type.” |
| `2013` | Invalid parameters / required fields missing.[5] | **No fixed status documented for the native image endpoint.** | `{"base_resp":{"status_code":2013,"status_msg":"invalid params"}}` (shape is MiniMax-style; exact message can vary).[5][7] | No; correct the request. | “The image request parameters were invalid.” |

The user-facing strings are implementation suggestions, not provider text. A
client should log the original `status_msg`, MiniMax code, HTTP status, and
request `id` where available. The official error reference says to provide the
`trace_id` header when contacting support.[5]

## Limits

### Rate limits and concurrency

MiniMax's current public rate-limit table gives Image Generation / `image-01` a
limit of **10 RPM** (requests per minute).[4] The Image-01 launch announcement
also states up to 9 images per request and up to 90 images per session, which
explains the per-request `n` range of 1–9 but is not itself a separate API
concurrency quota.[10][7][8]

I found **no documented image-generation concurrent-request (CONN) limit** in
the official image reference, OpenAPI schema, or rate-limit table. Do not infer
one from the 10 RPM number. A bot can queue one request at a time, and the
function below is a single image request; a server-level semaphore is still
reasonable if multiple users are admitted, but the exact supported concurrency
is not published here.[1][2][4]

The rate-limit page also says limits can depend on account/model/interface, and
the Token Plan FAQ notes dynamic throttling and rolling/windowed quotas. Treat
10 RPM as the documented baseline, not a guarantee during peak traffic.[4]

### Prompt and image limits

- `prompt`: maximum **1500 characters**.[1][2][7][8]
- `n`: 1–9 images per request; default 1.[1][2][7][8]
- Reference image: one reference per request, according to the image guide.[3]
- Reference `image_file`: public URL or Base64 data URL; OpenAPI lists
  JPG/JPEG/PNG and less than 10 MB.[2][8]
- `width`/`height`: 512–2048, each divisible by 8, only effective for
  `image-01`; dimensions and `aspect_ratio` are mutually alternative in the
  sense that `aspect_ratio` takes priority.[1][2][7][8]

### Aspect ratios

The current official reference supports exactly:[1][2][7][8]

| `aspect_ratio` | Default output dimensions |
|---|---:|
| `1:1` | 1024×1024 |
| `16:9` | 1280×720 |
| `4:3` | 1152×864 |
| `3:2` | 1248×832 |
| `2:3` | 832×1248 |
| `3:4` | 864×1152 |
| `9:16` | 720×1280 |
| `21:9` | 1344×576 |

There is no `9:21` or arbitrary-ratio value in the current image OpenAPI
schema. The official docs have not documented `1:2` or `2:1` for this image
endpoint. If a bot user requests another ratio, reject it or map it explicitly
rather than silently sending an unsupported value.[7][8]

### URL lifetime and latency/timeout

- **URL lifetime:** URL output expires in **24 hours**.[1][2][7][8] Download
  and persist it before expiry; the helper below downloads the first image
  synchronously before returning bytes.
- **Typical latency:** I found no official numeric average or p95 latency for
  native `image-01` generation. Do not claim a provider SLA from the docs.
- **Recommended `httpx` timeout:** no official image-generation latency
  figure or timeout is published. The official multi-language reference
  examples show a 30-second cURL timeout, while an upstream Hermes integration
  example uses 120 seconds.[1][2][12] For a bot, use a bounded timeout such as
  120 seconds and retry only transient transport/server errors; this is an
  engineering recommendation, not a MiniMax guarantee.
- **Retries:** the requested helper retries x2 (three total attempts). It
  retries network/5xx/408/504 and MiniMax `1002`; it does not retry
  `1004`, `1008`, `1026`, or `2049` unchanged.

## Model selection

Use **`image-01`** for the general, supported image-generation model. The
current image-to-image OpenAPI enum also lists `image-01-live`, but the public
API overview's model list names `image-01`, the text-to-image OpenAPI enum only
allows `image-01`, and the official examples use `image-01`. Therefore the
safe drop-in default is `image-01`; treat `image-01-live` as a possibly
account/region-dependent option requiring a live account test, not as a
replacement that is guaranteed available for every key.[1][2][6][7][8][9]

## Python `httpx` example

The following is a self-contained async function. It uses only `httpx` and the
Python standard library, is configured for the repository's declared
`httpx>=0.27`, requests exactly one URL-format image, downloads the first
`data.image_urls` result, maps MiniMax body errors to typed exceptions, and
performs at most two retries (x2; three total attempts).

```python
from __future__ import annotations

import asyncio
import base64
import binascii
import os
import random
from typing import Any

import httpx

MINIMAX_IMAGE_URL = "https://api.minimax.io/v1/image_generation"
MINIMAX_IMAGE_MODEL = "image-01"
SUPPORTED_ASPECT_RATIOS = {
    "1:1", "16:9", "4:3", "3:2", "2:3", "3:4", "9:16", "21:9",
}

class MiniMaxImageError(RuntimeError):
    """Base error for MiniMax image generation."""

class MiniMaxConfigurationError(MiniMaxImageError):
    pass

class MiniMaxAuthenticationError(MiniMaxImageError):
    pass

class MiniMaxContentError(MiniMaxImageError):
    pass

class MiniMaxBalanceError(MiniMaxImageError):
    pass

class MiniMaxRateLimitError(MiniMaxImageError):
    pass

class MiniMaxResponseError(MiniMaxImageError):
    pass


def _code_and_message(body: dict[str, Any], http_status: int) -> tuple[int | None, str]:
    base = body.get("base_resp") or {}
    code = base.get("status_code")
    msg = str(base.get("status_msg") or body.get("error") or "unknown error")
    # A few proxy/gateway versions put a numeric code in the top-level error.
    if code is None and isinstance(body.get("error"), (str, int)):
        raw = str(body["error"])
        if raw.isdigit():
            code = int(raw)
    return (int(code) if code is not None else None, msg)


def _raise_for_minimax_body(body: dict[str, Any], http_status: int) -> None:
    code, msg = _code_and_message(body, http_status)
    if code == 0:
        return
    if code in (1004, 2049):
        raise MiniMaxAuthenticationError(f"MiniMax authentication failed ({code}): {msg}")
    if code == 1008:
        raise MiniMaxBalanceError(f"MiniMax balance/quota insufficient ({code}): {msg}")
    if code == 1026:
        raise MiniMaxContentError(f"MiniMax blocked the input ({code}): {msg}")
    if code == 1002:
        raise MiniMaxRateLimitError(f"MiniMax rate limited the request ({code}): {msg}")
    raise MiniMaxResponseError(
        f"MiniMax request failed (HTTP {http_status}, code {code}): {msg}"
    )


async def _download_image(
    client: httpx.AsyncClient, image_url: str, *, timeout: httpx.Timeout
) -> bytes:
    """Follow redirects, require an image response, and return decoded bytes."""
    response = await client.get(image_url, follow_redirects=True, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type and not content_type.startswith("image/"):
        raise MiniMaxResponseError(
            f"Generated URL returned non-image content-type {content_type!r}"
        )
    data = response.content
    if not data:
        raise MiniMaxResponseError("Generated image URL returned an empty file")
    return data


async def generate(
    prompt: str,
    aspect: str,
    ref_url: str | None = None,
    *,
    api_key: str | None = None,
) -> bytes:
    """Generate one image and return its bytes.

    ``aspect`` must be one of MiniMax's documented native aspect ratios.
    ``ref_url`` is an optional public image URL; the native endpoint accepts
    only one character subject reference per request. The API key defaults
    to ``MINIMAX_API_KEY`` and is never sent in the request body.
    """
    prompt = (prompt or "").strip()
    aspect = (aspect or "").strip()
    if not prompt:
        raise ValueError("prompt must be a non-empty string")
    if aspect not in SUPPORTED_ASPECT_RATIOS:
        allowed = ", ".join(sorted(SUPPORTED_ASPECT_RATIOS))
        raise ValueError(f"unsupported aspect ratio {aspect!r}; use one of: {allowed}")
    if len(prompt) > 1500:
        raise ValueError("prompt exceeds MiniMax's 1500-character limit")
    if ref_url is not None and not ref_url.strip():
        ref_url = None

    key = api_key or os.getenv("MINIMAX_API_KEY")
    if not key:
        raise MiniMaxConfigurationError("MINIMAX_API_KEY is not set")
    key = key.strip()
    if not key:
        raise MiniMaxConfigurationError("MINIMAX_API_KEY is empty")

    payload: dict[str, Any] = {
        "model": MINIMAX_IMAGE_MODEL,
        "prompt": prompt,
        "aspect_ratio": aspect,
        "response_format": "url",
        "n": 1,
        "prompt_optimizer": True,
    }
    if ref_url:
        payload["subject_reference"] = [{
            "type": "character",
            "image_file": ref_url,
        }]

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # 120 seconds is an application recommendation, not an official MiniMax SLA.
    # A shorter value can be used for a latency-sensitive product.
    timeout = httpx.Timeout(120.0, connect=10.0)
    transport_retries = httpx.AsyncHTTPTransport(retries=0)

    last_transport_error: Exception | None = None
    async with httpx.AsyncClient(
        timeout=timeout,
        transport=transport_retries,
        headers=headers,
    ) as client:
        for attempt in range(3):  # initial attempt + two retries
            try:
                response = await client.post(MINIMAX_IMAGE_URL, json=payload)
                # The native endpoint may return HTTP 200 for an application error.
                if response.status_code >= 400:
                    try:
                        body: dict[str, Any] = response.json()
                    except ValueError:
                        raise MiniMaxResponseError(
                            f"MiniMax HTTP {response.status_code}: {response.text[:500]}"
                        ) from None
                    _raise_for_minimax_body(body, response.status_code)

                try:
                    body = response.json()
                except ValueError as exc:
                    raise MiniMaxResponseError(
                        f"MiniMax returned non-JSON HTTP {response.status_code} response"
                    ) from exc
                if not isinstance(body, dict):
                    raise MiniMaxResponseError("MiniMax response JSON was not an object")

                _raise_for_minimax_body(body, response.status_code)
                data = body.get("data")
                if not isinstance(data, dict):
                    raise MiniMaxResponseError("MiniMax response is missing object field 'data'")
                urls = data.get("image_urls")
                if not isinstance(urls, list) or not urls or not isinstance(urls[0], str):
                    raise MiniMaxResponseError(
                        "MiniMax response is missing data.image_urls[0]"
                    )
                return await _download_image(client, urls[0], timeout=timeout)

            except MiniMaxAuthenticationError:
                raise  # never retry invalid credentials
            except MiniMaxBalanceError:
                raise  # balance must be fixed, not retried
            except MiniMaxContentError:
                raise  # do not retry an unchanged safety-blocked request
            except MiniMaxRateLimitError:
                if attempt >= 2:
                    raise
                await asyncio.sleep(2 ** attempt)  # 1s, then 2s
            except (httpx.HTTPError, MiniMaxResponseError) as exc:
                last_transport_error = exc
                if attempt >= 2:
                    raise
                # Avoid synchronized retries; cap the additional jitter.
                await asyncio.sleep(min(8.0, 0.5 * (2 ** attempt)) + random.random())

    raise MiniMaxResponseError(
        f"image generation failed after retries: {last_transport_error}"
    )
```

The helper deliberately requests URL output rather than base64. It maps the
body's `base_resp.status_code` before trusting the HTTP status, handles the
known auth/balance/content/rate-limit classes without unsafe retries, and
downloads the generated asset before the documented 24-hour URL expiry. The
120-second timeout is a conservative bot-side choice; the official cURL example
uses 30 seconds and the Hermes integration example uses 120 seconds, so neither
is a published latency guarantee.[1][2][12]

## Sources

[1] https://platform.minimax.io/docs/api-reference/image-generation-t2i
[2] https://platform.minimax.io/docs/api-reference/image-generation-i2i
[3] https://platform.minimax.io/docs/guides/image-generation
[4] https://platform.minimax.io/docs/guides/rate-limits
[5] https://platform.minimax.io/docs/api-reference/errorcode
[6] https://platform.minimax.io/docs/api-reference/api-overview
[7] https://platform.minimax.io/docs/api-reference/image/generation/api/text-to-image.json
[8] https://platform.minimax.io/docs/api-reference/image/generation/api/image-to-image.json
[9] https://docs.openclaw.ai/providers/minimax
[10] https://www.minimax.io/news/image-01
[11] https://github.com/openclaw/openclaw/issues/61149
[12] https://github.com/NousResearch/hermes-agent/pull/10389/files
[13] https://minimax-ai.chat/guide/minimax-api-error-codes
