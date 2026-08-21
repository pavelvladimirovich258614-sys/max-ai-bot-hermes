"""`/status` slash command (F0.2, 2026-08-21).

Shows a diagnostics card so the user can see whether the bot can talk to
Hermes + the configured LLM providers, and what failed recently.

What it reports:
  * Hermes mode (auto / http / cli / none) and whether a Hermes RZA peer is
    configured.
  * Whether LLM primary and LLM fallback API keys are set in .env.
  * Last 5 error lines from the orchestrator's fallback chain.
  * Timestamp of the last successful orchestrator answer.
  * Last fallback chain in a compact one-line-per-step form.

Buttons:
  * [🔄 Retry]          — re-renders the status card (re-checks everything).
  * [🏠 В меню]         — returns to the main menu.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from maxapi import Dispatcher
from maxapi.filters import F
from maxapi.types import CallbackButton, Command, MessageCallback, MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder  # type: ignore[import-not-found]

from app.max.handlers.deps import Deps
from app.max.ui import replace_callback_message

logger = logging.getLogger("maxbot.status")

# Callback payload for the [🔄 Retry] button.
RETRY_PAYLOAD = "status:retry"


def _format_chain(steps: list[dict]) -> list[str]:
    """Render a one-line-per-step view of the last fallback chain."""
    if not steps:
        return ["(пока ничего не выполнялось)"]
    out: list[str] = []
    for s in steps:
        marker = "✅" if s.get("ok") else "❌"
        provider = s.get("provider", "?")
        latency = s.get("latency_s", 0.0)
        reason = s.get("reason", "")
        line = f"{marker} {provider} ({latency:.2f}s)"
        if reason:
            line += f" — {reason}"
        out.append(line)
    return out


def _format_ts(ts: float | None) -> str:
    if ts is None:
        return "(никогда)"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def build_status_text(health: dict[str, Any]) -> str:
    """Compose the human-readable status card (also reused by the test)."""
    hermes_mode = health.get("hermes_mode", "unknown")
    primary_set = health.get("llm_primary_set", False)
    fallback_set = health.get("llm_fallback_set", False)
    recent_errors = health.get("recent_errors", []) or []
    last_success_ts = health.get("last_success_ts")
    chain = health.get("last_chain", []) or []

    lines: list[str] = [
        "📊 СТАТУС БОТА",
        "",
        f"• Hermes mode: {hermes_mode}",
        f"• LLM primary key:  {'✅ задан' if primary_set else '❌ НЕ задан'}",
        f"• LLM fallback key: {'✅ задан' if fallback_set else '❌ НЕ задан'}",
        f"• Последний успех: {_format_ts(last_success_ts)}",
        "",
        "Последняя цепочка fallback:",
        *_format_chain(chain),
    ]
    if recent_errors:
        lines.append("")
        lines.append("Последние ошибки:")
        lines.extend(f"  • {err}" for err in recent_errors)
    return "\n".join(lines)


def _build_retry_keyboard() -> list:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="🔄 Retry", payload=RETRY_PAYLOAD))
    b.row(CallbackButton(text="🏠 В меню", payload="home"))
    return [b.as_markup()]


def register(dp: Dispatcher, deps: Deps) -> None:
    """Hook `/status` (text) + the [🔄 Retry] button (callback)."""

    @dp.message_created(Command("status"))
    async def cmd_status(event: MessageCreated) -> None:
        # Orchestrator.health() is async for forward-compat (it may grow to
        # ping Hermes in the future); currently does no I/O.
        health = await deps.orchestrator.health()
        text = build_status_text(health)
        await event.message.answer(
            text,
            attachments=_build_retry_keyboard(),
        )

    @dp.message_callback(F.callback.payload == RETRY_PAYLOAD)
    async def on_retry(event: MessageCallback) -> None:
        # Re-render the status card. The [🔄 Retry] button is a "re-check
        # now"; we don't keep the last task body in Orchestrator, so we just
        # refresh the diagnostics view.
        health = await deps.orchestrator.health()
        await replace_callback_message(
            event,
            build_status_text(health),
            attachments=_build_retry_keyboard(),
        )
