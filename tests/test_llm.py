"""LLM client unit tests (no network)."""
import asyncio

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
