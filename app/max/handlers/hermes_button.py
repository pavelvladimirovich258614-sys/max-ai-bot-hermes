"""Handler для кнопки [🤖 Hermes] в main_menu (Feature V3, 2026-08-19).

Три сценария: контент-план, исследование, своя задача. Каждый сценарий:

  1. callback_data='hermes:<scenario>' → ставит user state и просит ввод.
  2. следующее сообщение пользователя (text) → spawn HermesSession в фоне.
  3. dispatcher публикует прогресс + финальный результат.

Если Hermes peer RZA не зарегистрирован (текущее состояние Pavel'я),
spawn сначала попробует CLI, увидит ошибку и автоматически уйдёт на
in-process LLM fallback через Orchestrator.
"""
from __future__ import annotations

import logging

from maxapi import Dispatcher
from maxapi.filters import F
from maxapi.types import MessageCallback, MessageCreated

from app.hermes.dispatcher import HermesDispatcher
from app.max.handlers.deps import Deps
from app.max.handlers.state_filters import StateActionFilter
from app.max.keyboards import callback_message, hermes_submenu_keyboard
from app.max.state import clear_state, get_state, set_state
from app.max.ui import replace_callback_message

logger = logging.getLogger("maxbot.hermes_button")

# FSM-state action keys
_STEP_PROMPT = "hermes:await_task"

_SCENARIO_TO_STATE: dict[str, str] = {
    "plan": "hermes:await_task:plan",
    "research": "hermes:await_task:research",
    "custom": "hermes:await_task:custom",
}


def register(dp: Dispatcher, deps: Deps) -> None:
    """Подключается как обычный maxapi dispatcher."""

    @dp.message_callback(F.callback.payload.startswith("hermes"))
    async def on_hermes_callback(event: MessageCallback) -> None:
        payload = (event.callback.payload or "").strip()
        chat_id, user_id = event.get_ids()

        if payload == "hermes":
            await replace_callback_message(
                event,
                "🤖 HERMES\n\n"
                "Запускаю расширенную Hermes-сессию с маршрутизацией задачи.\n"
                "Что сделать?",
                attachments=hermes_submenu_keyboard(),
            )
            return

        if payload in ("hermes:plan", "hermes:research", "hermes:custom"):
            scenario = payload[len("hermes:"):]  # 'plan' | 'research' | 'custom'
            prompts = {
                "plan": (
                    "📊 **Контент-план через Hermes**\n\n"
                    "Введи тему/нишу и количество дней.\n"
                    "**Пример:** `7 дней | AI-инструменты для маркетинга`"
                ),
                "research": (
                    "📝 **Исследование через Hermes**\n\n"
                    "Введи тему или URL страницы.\n"
                    "**Пример:** `влияние ИИ на копирайтинг в 2026`"
                ),
                "custom": (
                    "🎯 **Своя задача для Hermes**\n\n"
                    "Опиши задачу — Hermes сам выберет роль и инструменты.\n"
                    "**Пример:** `придумай 5 заголовков для статьи про MAX-ботов`"
                ),
            }
            set_state(user_id, _SCENARIO_TO_STATE[scenario])
            await replace_callback_message(
                event,
                prompts[scenario],
                attachments=hermes_submenu_keyboard(),
            )
            return

    @dp.message_created(StateActionFilter(prefixes=("hermes:await_task:",)))
    async def on_hermes_text(event: MessageCreated) -> None:
        text = (event.message.body.text or "").strip()
        if not text or text.startswith("/"):
            return
        chat_id, user_id = event.get_ids()
        st = get_state(user_id)
        if st is None:
            return  # not in Hermes flow
        action = st["action"]
        if not action.startswith("hermes:await_task:"):
            return
        scenario = action[len("hermes:await_task:"):]  # 'plan' | 'research' | 'custom'
        clear_state(user_id)

        # Если у пользователя уже есть активная сессия — не запускаем вторую.
        dispatcher: HermesDispatcher | None = getattr(deps, "hermes", None)
        if dispatcher is None:
            # Старый deploy без HermesDispatcher — fallback на понятное сообщение.
            logger.error("hermes_button: dispatcher not initialised on deps")
            await _send(
                deps, chat_id,
                "⚠️ Hermes временно недоступен. Перезапусти бота — `/start`.",
                attachments=hermes_submenu_keyboard(),
            )
            return
        if dispatcher.has_active(user_id):
            await _send(
                deps, chat_id,
                "⏳ У тебя уже запущена Hermes-сессия. Дождись её завершения "
                "(прогресс пишется отдельными сообщениями).",
                attachments=hermes_submenu_keyboard(),
            )
            return

        try:
            sess = await dispatcher.spawn(
                chat_id=chat_id, user_id=user_id,
                task=text, scenario=scenario,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("hermes spawn failed: %s", e)
            await _send(
                deps, chat_id,
                f"⚠️ Не удалось запустить Hermes: {e}",
                attachments=hermes_submenu_keyboard(),
            )
            return
        # Progress is shown by HermesDispatcher in one editable message.
        return


async def _ack(event: MessageCallback) -> None:
    """Acknowledge the inline button without a chat message."""
    # V5 (2026-08-19): MessageForCallback("") becomes a blank white bubble
    # in MAX. A notification acknowledges the click without touching chat.
    await event.answer(notification="Готово")


async def _send(deps: Deps, chat_id: int, text: str, *, attachments=None) -> None:
    """Send via MarkdownSender so MAX renders Markdown."""
    from app.max.formatting import MarkdownSender
    sender = MarkdownSender(deps.bot)  # type: ignore[arg-type]
    try:
        await sender.send(chat_id, text, attachments=attachments)
    except Exception as e:  # noqa: BLE001
        logger.warning("hermes_button _send failed: %s", e)