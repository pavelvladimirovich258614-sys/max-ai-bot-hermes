"""Free-text chat: any non-command message -> dialog with the LLM.

Context (last 10 messages) pulled from SQLite. If a menu FSM state is active,
the menu handler consumes the text first (it clears state), so this handler
checks for an active state and yields.

F0.3 (2026-08-21): before falling through to the chat role, run a tiny
keyword-based intent router. If the message looks like "напиши пост про ..."
or "разбери ссылку https://...", we set the matching menu FSM state and
reply with the same description the user would see after pressing the
corresponding button. The next user message is then consumed by the proper
executor (do_research, do_copy, do_analyze, …).

This keeps the bot useful when Hermes RZA is offline AND the LLM fallback
chain is also down: at least the bot can still ask for the right input
instead of dead-ending.
"""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.filters import F
from maxapi.types import MessageCreated

from app.max.executors import _safe_orchestrator_run, _send_final
from app.max.handlers.deps import Deps
from app.max.handlers.state_filters import NoActiveStateFilter
from app.max.intent_router import route_intent
from app.max.state import get_state, set_state
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

        # F0.3: try to route the message to a real menu flow before sending
        # it to the LLM. If we find a match, set the state and reply with
        # the matching COMMAND_DESCRIPTIONS text so the user knows what to
        # type next. The actual executor runs on the *next* user message.
        intent = route_intent(text)
        if intent is not None:
            from app.max.descriptions import COMMAND_DESCRIPTIONS
            set_state(user_id, intent.next_state)
            description = COMMAND_DESCRIPTIONS.get(
                intent.command_payload,
                f"Принято как «{intent.name}». Введите запрос.",
            )
            # Reuse the executor's plain-send helper so formatting is uniform.
            from app.max.executors import _safe_send
            from app.max.keyboards import main_menu_keyboard
            await _safe_send(event, description, attachments=main_menu_keyboard())
            return

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
