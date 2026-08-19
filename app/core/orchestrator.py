"""Core orchestrator: routes a MAX request to Hermes (RZA) or direct LLM.

This is the "RZA brain" inside the bot. The flow mirrors the Wu-Tang handoff
contract: decide role -> try Hermes -> fall back to local LLM if Hermes is down.

ROLE -> specialist mapping (for reference / logging):
  researcher   -> GZA
  copywriter   -> Cappadonna -> Ghostface (polish)
  marketer     -> Cappadonna (swarm)
  ideator      -> Cappadonna
  analyzer     -> GZA
  prompt_engineer -> Masta Killa
  chat         -> RZA (chat agent)
  image_prompt -> Cappadonna (visual director)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.config import Settings
from app.hermes.client import HermesClient
from app.llm.client import LLMClient
from app.db.storage import Storage

logger = logging.getLogger("maxbot.orchestrator")

ROLE_SYSTEM_MODULES = {
    "researcher": "app.llm.prompts.researcher",
    "copywriter": "app.llm.prompts.copywriter",
    "marketer": "app.llm.prompts.marketer",
    "ideator": "app.llm.prompts.ideator",
    "analyzer": "app.llm.prompts.analyzer",
    "prompt_engineer": "app.llm.prompts.prompt_engineer",
    "chat": "app.llm.prompts.chat",
    "image_prompt": "app.llm.prompts.image_prompt",
}

_ROLE_CACHE: dict[str, str] = {}

# Skills map (per docs/enhancement-design.md). Дописываем в SYSTEM_PROMPT роли,
# чтобы LLM знал, какие приёмы применять.
ROLE_SKILLS: dict[str, list[str]] = {
    "researcher": [
        "grounded-citations", "competitor-news-monitor", "blogwatcher",
        "arxiv", "llm-wiki", "blocked-page-recovery",
    ],
    "copywriter": [
        # Обязательные 5 + опц.
        "humanizer", "baoyu-infographic", "document-to-action-items",
        "meeting-action-items", "popular-web-designs", "claude-design",
    ],
    "prompt_engineer": [
        "plan", "test-driven-development", "systematic-debugging",
        "requesting-code-review", "hermes-agent-skill-authoring",
    ],
    "analyzer": [
        "ocr-and-documents", "pdf", "youtube-content",
        "document-to-action-items", "nano-pdf",
    ],
}

# Markdown-format skill — same content for every role so the LLM produces
# output that MAX actually renders (Markdown, no # headers, no ``` blocks).
_MARKDOWN_SKILL_PATH = Path(__file__).resolve().parent.parent / "llm" / "skills" / "markdown_format.md"
_MARKDOWN_SKILL_BLOCK: str | None = None


def _load_markdown_skill() -> str:
    """Read the markdown-format skill file once and cache it.

    Returns an empty string if the file is missing (so we don't crash dev
    environments where someone deleted it).
    """
    global _MARKDOWN_SKILL_BLOCK
    if _MARKDOWN_SKILL_BLOCK is not None:
        return _MARKDOWN_SKILL_BLOCK
    try:
        _MARKDOWN_SKILL_BLOCK = _MARKDOWN_SKILL_PATH.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("markdown_format skill not loaded: %s", e)
        _MARKDOWN_SKILL_BLOCK = ""
    return _MARKDOWN_SKILL_BLOCK


def _system_prompt(role: str) -> str:
    if role not in _ROLE_CACHE:
        try:
            import importlib

            mod = importlib.import_module(ROLE_SYSTEM_MODULES.get(role, "app.llm.prompts.chat"))
            base = getattr(mod, "SYSTEM_PROMPT", "")
        except Exception:  # noqa: BLE001
            base = ""
        skills = ROLE_SKILLS.get(role) or []
        if skills and base:
            block = "\n\n## Доступные навыки (skills)\n" + "\n".join(f"- {s}" for s in skills)
            _ROLE_CACHE[role] = base + block
        else:
            _ROLE_CACHE[role] = base
    prompt = _ROLE_CACHE[role]
    # Append the Markdown-format skill once per role's first request — it
    # belongs to the system contract, not per-call state.
    skill = _load_markdown_skill()
    if skill and "## SKILL: Markdown" not in prompt:
        prompt = prompt + "\n\n" + skill
    return prompt


class Orchestrator:
    def __init__(self, settings: Settings, llm: LLMClient, storage: Storage) -> None:
        self._s = settings
        self._llm = llm
        self._storage = storage
        self._hermes = HermesClient(settings)

    async def aclose(self) -> None:
        await self._hermes.aclose()

    async def run(
        self,
        role: str,
        task: str,
        context: Optional[dict] = None,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> str:
        """Run a task through Hermes (preferred) or the direct LLM (fallback)."""
        # 1) Try Hermes first (RZA routes to the right specialist).
        hermes_answer = await self._hermes.route(role, task, context)
        if hermes_answer:
            logger.info("orchestrate role=%s via=hermes", role)
            return hermes_answer

        # 2) Fallback: direct LLM call.
        logger.info("orchestrate role=%s via=llm-fallback", role)
        messages = [{"role": "user", "content": task}]
        # For chat, append recent history for context.
        if role == "chat" and chat_id is not None:
            history = await self._storage.get_session_context(chat_id, user_id, limit=10)
            if history:
                messages = history + messages
        try:
            return await self._llm.chat(messages, role=role, system=_system_prompt(role))
        except Exception as e:  # noqa: BLE001
            logger.error("LLM fallback failed for role=%s: %s", role, e)
            return (
                "⚠️ Не удалось получить ответ: Hermes недоступен, а локальный LLM "
                "тоже не ответил. Проверьте ключи LLM в .env или доступность Hermes."
            )
