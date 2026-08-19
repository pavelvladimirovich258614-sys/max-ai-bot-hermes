"""LLM client unit tests (no network)."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings
from app.llm.client import LLMClient


def test_build_client():
    s = Settings(_env_file=None)
    client = LLMClient(s)
    assert client is not None
    asyncio.run(client.aclose())


def test_system_prompt_resolution():
    # Importing the orchestrator should not crash; system prompts exist.
    from app.core.orchestrator import _system_prompt

    assert _system_prompt("researcher")
    assert _system_prompt("nonsense_role")  # falls back to chat prompt


@pytest.mark.asyncio
async def test_primary_timeout_switches_to_fallback_without_retrying_primary():
    s = Settings(_env_file=None)
    s.llm_primary_api_key = "primary-test-key"
    s.llm_fallback_api_key = "fallback-test-key"
    s.llm_primary_style = "anthropic"
    s.llm_fallback_style = "openai"
    client = LLMClient(s)
    client._call_anthropic = AsyncMock(side_effect=httpx.ReadTimeout("slow primary"))
    client._call_openai = AsyncMock(return_value="fallback answer")

    try:
        with patch("app.llm.client.asyncio.sleep", new=AsyncMock()):
            answer = await client.chat([{"role": "user", "content": "hello"}])
    finally:
        await client.aclose()

    assert answer == "fallback answer"
    assert client._call_anthropic.await_count == 1
    assert client._call_openai.await_count == 1
