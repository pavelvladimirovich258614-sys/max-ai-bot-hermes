"""Core orchestrator: routes a MAX request to Hermes (RZA) or direct LLM.

This is the "RZA brain" inside the bot. The flow mirrors the Wu-Tang handoff
contract: decide role -> try Hermes -> fall back to local LLM if Hermes is down.

Fallback chain (F0.1, 2026-08-21):
  1) Hermes (RZA) over HTTP or CLI
  2) LLM primary (e.g. MiniMax) — LLMClient internally falls back to
  3) LLM fallback (e.g. StepFun) on the same LLMClient.chat() call
  4) User-facing error message with /status hint if every step fails

Every step is recorded in self._last_chain so /status can report it.

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
import time
from collections import deque
from dataclasses import dataclass, field
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


@dataclass
class ChainStep:
    """One attempt in the fallback chain (F0.1)."""

    provider: str          # "hermes" | "llm_primary" | "llm_fallback"
    ok: bool
    reason: str = ""       # human-readable reason on failure
    latency_s: float = 0.0
    ts: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "reason": self.reason,
            "latency_s": round(self.latency_s, 3),
        }


@dataclass
class OrchestratorResult:
    """Result of one Orchestrator.run() call (F0.1).

    ``text`` is always a non-empty string the bot can return to the user.
    ``chain`` records every step of the fallback chain (Hermes → primary → fallback).
    ``ok`` is True iff at least one step succeeded.
    """

    text: str
    chain: list[ChainStep] = field(default_factory=list)
    ok: bool = True

    @property
    def via(self) -> Optional[str]:
        """Return the provider that produced the answer, or None if all failed."""
        for step in self.chain:
            if step.ok:
                return step.provider
        return None


class Orchestrator:
    def __init__(self, settings: Settings, llm: LLMClient, storage: Storage) -> None:
        self._s = settings
        self._llm = llm
        self._storage = storage
        self._hermes = HermesClient(settings)
        # F0.1: rolling buffer of the last 5 results so /status can show them.
        self._last_results: deque[OrchestratorResult] = deque(maxlen=5)
        # F0.1: ring buffer of the most recent error lines (across all calls).
        self._recent_errors: deque[str] = deque(maxlen=5)
        # F0.1: timestamp of the last successful answer, for /status.
        self._last_success_ts: Optional[float] = None

    async def aclose(self) -> None:
        await self._hermes.aclose()

    # ---- public diagnostics (F0.2) ----

    @property
    def last_success_ts(self) -> Optional[float]:
        return self._last_success_ts

    def recent_errors(self) -> list[str]:
        """Return up to 5 most-recent error lines from the chain."""
        return list(self._recent_errors)

    def last_chain(self) -> list[ChainStep]:
        """Return the chain of the most-recent run(), or [] if none yet."""
        if not self._last_results:
            return []
        return list(self._last_results[-1].chain)

    async def health(self) -> dict:
        """Synchronous-ish snapshot used by /status (F0.2).

        Returns a dict with: hermes_mode, llm_primary_set, llm_fallback_set,
        recent_errors, last_success_ts (epoch seconds, or None), last_chain.
        """
        return {
            "hermes_mode": self._s.hermes_mode,
            "llm_primary_set": bool(
                self._s.llm_primary_api_key or self._s.llm_api_key
            ),
            "llm_fallback_set": bool(self._s.llm_fallback_api_key),
            "recent_errors": self.recent_errors(),
            "last_success_ts": self._last_success_ts,
            "last_chain": [s.to_dict() for s in self.last_chain()],
        }

    # ---- main entry point (refactored for F0.1) ----

    async def run(
        self,
        role: str,
        task: str,
        context: Optional[dict] = None,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> str:
        """Run a task through a three-level fallback chain.

        Levels (in order):
          1) Hermes (RZA) — preferred path
          2) LLM primary — LLMClient internally tries fallback on failure
          3) If both LLM providers fail, return a user-facing error message
             that points at /status.
        """
        chain: list[ChainStep] = []

        # ---- 1) Hermes ----
        t0 = time.monotonic()
        try:
            hermes_answer = await self._hermes.route(role, task, context)
        except Exception as e:  # noqa: BLE001
            hermes_answer = None
            chain.append(
                ChainStep(
                    provider="hermes",
                    ok=False,
                    reason=f"exception: {type(e).__name__}: {e}",
                    latency_s=time.monotonic() - t0,
                )
            )
            logger.warning("orchestrate hermes raised role=%s: %s", role, e)
        else:
            if hermes_answer:
                chain.append(
                    ChainStep(
                        provider="hermes",
                        ok=True,
                        latency_s=time.monotonic() - t0,
                    )
                )
                result = OrchestratorResult(text=hermes_answer, chain=chain, ok=True)
                self._record_result(result)
                logger.info("orchestrate role=%s via=hermes", role)
                return hermes_answer
            chain.append(
                ChainStep(
                    provider="hermes",
                    ok=False,
                    reason="no answer (mode=%s, returned=None)" % self._s.hermes_mode,
                    latency_s=time.monotonic() - t0,
                )
            )

        # ---- 2) Direct LLM (primary, with internal fallback to secondary) ----
        logger.info("orchestrate role=%s via=llm-fallback", role)
        messages = [{"role": "user", "content": task}]
        # For chat, append recent history for context.
        if role == "chat" and chat_id is not None:
            history = await self._storage.get_session_context(chat_id, user_id, limit=10)
            if history:
                messages = history + messages
        t0 = time.monotonic()
        try:
            answer = await self._llm.chat(messages, role=role, system=_system_prompt(role))
        except Exception as e:  # noqa: BLE001
            # LLMClient.chat() already tried primary → fallback internally and raised
            # LLMError; we record both providers as failed.
            err_text = f"llm_primary+fallback: {type(e).__name__}: {e}"
            chain.append(
                ChainStep(
                    provider="llm_primary",
                    ok=False,
                    reason=err_text,
                    latency_s=time.monotonic() - t0,
                )
            )
            chain.append(
                ChainStep(
                    provider="llm_fallback",
                    ok=False,
                    reason="also failed (see llm_primary reason above)",
                    latency_s=0.0,
                )
            )
            logger.error("LLM fallback chain failed for role=%s: %s", role, e)
            err_msg = self._all_failed_message(chain)
            result = OrchestratorResult(text=err_msg, chain=chain, ok=False)
            self._record_result(result)
            return err_msg

        # LLM chain succeeded — record the providers that worked. We don't know
        # which one (primary or fallback) actually answered, so we mark the
        # one we *tried* (primary) as ok, since the LLMClient transparently
        # fell back if needed.
        chain.append(
            ChainStep(
                provider="llm_primary",
                ok=True,
                latency_s=time.monotonic() - t0,
            )
        )
        result = OrchestratorResult(text=answer, chain=chain, ok=True)
        self._record_result(result)
        logger.info("orchestrate role=%s via=llm", role)
        return answer

    # ---- helpers ----

    def _record_result(self, result: OrchestratorResult) -> None:
        """Push one OrchestratorResult into the rolling buffer + error ring."""
        self._last_results.append(result)
        if result.ok:
            self._last_success_ts = time.time()
        else:
            # Record a short, human-readable line for /status.
            failed = [s.provider for s in result.chain if not s.ok]
            if failed:
                self._recent_errors.append(
                    f"{time.strftime('%H:%M:%S')} "
                    f"failed_chain={' → '.join(failed)}"
                )

    def _all_failed_message(self, chain: list[ChainStep]) -> str:
        """Build the user-facing error message when the whole chain failed.

        F0.1 contract: the message names the three tried levels, hints at the
        most likely root causes, and points at /status for diagnostics.
        """
        failed_providers = [s.provider for s in chain if not s.ok]
        tried = " → ".join(failed_providers) if failed_providers else "(none)"

        # Build a short hint per missing config; we never echo actual key values.
        hints: list[str] = []
        if not (self._s.llm_primary_api_key or self._s.llm_api_key):
            hints.append("LLM_PRIMARY_API_KEY задан?")
        if not self._s.llm_fallback_api_key:
            hints.append("LLM_FALLBACK_API_KEY задан?")
        if self._s.hermes_mode != "none":
            hints.append("Hermes RZA запущен (hermes peer dm rza работает)?")
        hint_line = ("Проверьте: " + "; ".join(hints) + ".") if hints else ""

        return (
            "⚠️ СЕРВИС ВРЕМЕННО НЕДОСТУПЕН\n\n"
            f"Попробовано: {tried}.\n"
            f"{hint_line}\n"
            "Команда /status покажет диагностику."
        )
