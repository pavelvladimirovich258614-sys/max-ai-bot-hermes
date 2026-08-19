"""Free-text chat: any non-command message -> dialog with the LLM.

Context (last 10 messages) pulled from SQLite. If a menu FSM state is active,
the menu handler consumes the text first (it clears state), so this handler
checks for an active state and yields.
"""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.filters import F
from maxapi.types import MessageCreated

from app.max.executors import _safe_orchestrator_run, _send_final
from app.max.handlers.deps import Deps
from app.max.handlers.state_filters import NoActiveStateFilter
from app.max.state import get_state
from app.max.ui import ProgressReporter


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(F.message.body.text, NoActiveStateFilter())
    async def free_chat(event: MessageCreated) -> None:
        text = event.message.body.text or ""
        if text.startswith("/"):
            return  # command handlers take over
        chat_id, user_id = event.get_ids()
        if get_state(user_id) is not None:
            return  # a menu flow is awaiting this input; menu handler owns it
        await deps.storage.add_message(chat_id, user_id, "user", text)
        # Single-line progress: send "🤖 Думаю…" and edit it to "✅ Готово…"
        # just before the final answer arrives. Matches the Hermes TUI
        # feel without spamming the chat with per-step messages.
        async with ProgressReporter(event, "🤖 Думаю…") as prog:
            answer = await _safe_orchestrator_run(
                deps,
                role="chat",
                task=text,
                context={"source": "max", "command": "free_chat"},
                chat_id=chat_id,
                user_id=user_id,
            )
            await prog.step("✅ Готово — формирую ответ…")
            await prog.flush()
        await _send_final(
            deps, event,
            role="chat", chat_id=chat_id, user_id=user_id, answer=answer,
        )