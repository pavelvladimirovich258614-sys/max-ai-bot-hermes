"""Button-driven menu: routes inline callbacks and collects the next text input.

Flow:
  * Callback 'research'/'copy'/'plan'/'analyze'/'ideate'/'prompt' -> set FSM state,
    ask the user for the corresponding input.
  * Callback 'post' -> show post submenu (my channels / manual id).
  * Callback 'post:manual' -> set FSM state, ask for "<channel_id> <text>".
  * Callback 'post:my_channels' -> list bot's channel subscriptions (POST /subscriptions).
  * Callback 'home' -> return to main menu (NEW message, not edit).
  * Free text while in a state -> run the corresponding executor, clear state.

Every callback is acknowledged with `send_callback(callback_id, message=...)`
where `message` is a valid `MessageForCallback` (built via
`keyboards.callback_message`). Passing `message=None` makes the MAX API reject
the request with 400 'message or notification required' — that was the bug that
broke every inline button.

Feature 2 (2026-08-19): when we want to navigate away from a result (e.g.
`home` after a LLM result), the new menu must come as a NEW message so the
previous result stays visible. We use the markdown sender for that path.
"""
from __future__ import annotations

import logging

from maxapi import Dispatcher
from maxapi.filters import F
from maxapi.types import MessageCreated, MessageCallback

from app.max.handlers.deps import Deps
from app.max.handlers.state_filters import StateActionFilter
from app.max.descriptions import COMMAND_DESCRIPTIONS
from app.max.keyboards import (
    CallbackButton,
    callback_message,
    main_menu_keyboard,
    post_submenu_keyboard,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder  # type: ignore[import-not-found]
from app.max.state import get_state, set_state, clear_state
from app.max.executors import (
    do_research, do_copy, do_plan, do_ideate, do_prompt, do_analyze, do_post,
)
from app.max.ui import header, replace_callback_message

logger = logging.getLogger("maxbot.menu")

_MENU_CALLBACK_PAYLOADS = frozenset({
    "home", "help", "post", "post:my_channels", "post:manual", "post:cancel",
    "research", "copy", "plan", "analyze", "ideate", "prompt", "restart",
})


async def _menu_reply(
    event: MessageCallback, text: str, *, attachments=None
) -> None:
    """Replace the clicked menu message in place without creating a new bubble.

    ``bot.send_callback(message=MessageForCallback(...))`` creates a separate
    chat bubble in the MAX desktop client. That is the blank/⌛ block Pavel saw.
    The SDK's ``event.answer(new_text=...)`` updates the original callback
    message instead, which is what menu navigation needs.
    """
    await replace_callback_message(event, text, attachments=attachments)


async def _ack_callback(event: MessageCallback) -> None:
    """Acknowledge a click without emitting or editing a chat message."""
    # notification is an ephemeral client toast, not a chat bubble.
    await event.answer(notification="Готово")


def register(dp: Dispatcher, deps: Deps) -> None:
    # ---------- callback routing ----------
    @dp.message_callback(F.callback.payload.in_(_MENU_CALLBACK_PAYLOADS))
    async def on_menu_callback(event: MessageCallback) -> None:
        payload = (event.callback.payload or "").strip()
        if not payload:
            return
        chat_id, user_id = event.get_ids()

        # Image flow lives in handlers/image_gen.py — if we see any image:*
        # payload here it means the image handler didn't match (or wasn't
        # registered). Better to be explicit than swallow it.
        if (
            payload == "image"
            or payload.startswith("image:")
            or payload == "hermes"
            or payload.startswith("hermes:")
        ):
            return

        if payload == "home":
            clear_state(user_id)
            await _menu_reply(
                event,
                header("🏠", "ГЛАВНОЕ МЕНЮ", [
                    "Выбери действие кнопкой ниже.",
                    "Или напиши сообщение — бот ответит в диалоге.",
                ]),
                attachments=main_menu_keyboard(),
            )
            return

        if payload == "help":
            await _menu_reply(event, header("❓", "ПОМОЩЬ", _help_text()),
                              attachments=main_menu_keyboard())
            return

        if payload == "post":
            await _menu_reply(
                event,
                header("📤", "ПОСТ В КАНАЛ", ["Куда опубликовать пост?"]),
                attachments=post_submenu_keyboard(),
            )
            return

        if payload == "post:my_channels":
            # B3 (2026-08-19): retry 3с (MAX API кэширует), затем user-friendly.
            channels = await _list_channels(deps, event)
            await _menu_reply(
                event,
                header("📋", "МОИ КАНАЛЫ", channels),
                attachments=post_submenu_keyboard(),
            )
            return

        if payload == "post:manual":
            # B2 (2026-08-19): state='post:awaiting' — следующий текст
            # обработает handlers/post.py и распарсит "<chat_id> <текст>".
            # V4 fix (2026-08-19): НЕ прикрепляем main_menu_keyboard —
            # кнопка [🏠 В меню] сбрасывает state и Pavel теряет ввод.
            # Вместо этого показываем ТОЛЬКО подсказку + [❌ Отмена] →
            # payload="post:cancel" (новый state-cancel).
            set_state(user_id, "post:awaiting")
            cancel_kb = InlineKeyboardBuilder()
            cancel_kb.row(CallbackButton(text="❌ Отмена", payload="post:cancel"))
            await _menu_reply(
                event,
                header("🔢", "РУЧНОЙ ВВОД CHAT_ID", [
                    "Введите: <channel_id> <текст>",
                    "Например: 123456 Новый пост про ИИ",
                    "",
                    "⚠️ Бот должен быть администратором канала.",
                    "Если канал не отвечает — добавьте бота через",
                    "Настройки канала → → Участники → → @id752703975446_3_bot → → Сделать админом.",
                ]),
                attachments=[cancel_kb.as_markup()],
            )
            return

        if payload == "post:cancel":
            # V4 (2026-08-19): пользователь отменил ввод chat_id вручную.
            clear_state(user_id)
            await _menu_reply(
                event,
                "❌ Ввод отменён. Возвращаюсь в главное меню.",
                attachments=main_menu_keyboard(),
            )
            return

        # action triggers that await text input.
        # Pavel (2026-08-19): подсказки были слишком сухие ("Введи тему...").
        # Теперь — подробные описания из COMMAND_DESCRIPTIONS, через
        # MarkdownSender (с format=markdown). Если MAX не рендерит markdown
        # в inline-callback ответах — пользователь хотя бы видит чистый текст
        # со звёздочками (plain-text fallback), что лучше чем ничего.
        if payload in ("research", "copy", "plan", "analyze", "ideate",
                       "prompt", "image", "post", "hermes"):
            description = COMMAND_DESCRIPTIONS.get(payload)
            if description:
                # image → state set in image_gen handler; Hermes → state
                # set in hermes_button handler. All others go into the
                # generic "<action>" state handled by free_chat.
                if payload == "image":
                    next_state = "image:ask_source"
                elif payload == "hermes":
                    next_state = None  # Hermes entry shows submenu, not text-input
                else:
                    next_state = payload
                if next_state is not None:
                    set_state(user_id, next_state)
                await _menu_reply(
                    event,
                    description,
                    attachments=main_menu_keyboard(),
                )
                return
            # Fallback for any payload not in COMMAND_DESCRIPTIONS.
            prompts = {
                "research": header("🔍", "RESEARCH", [
                    "Введи тему для исследования.",
                    "Бот вернёт глубокий бриф из 5-7 пунктов с источниками.",
                ]),
                "copy": header("✍️", "COPY", [
                    "Введи тему поста и стиль через разделитель «|».",
                    "Например: кофейня | мягкий стиль",
                ]),
                "plan": header("📅", "КОНТЕНТ-ПЛАН", [
                    "Укажи количество дней и нишу.",
                    "Например: 7 дней | кофейня",
                ]),
                "analyze": header("🔬", "ANALYZE", [
                    "Пришли URL страницы для разбора.",
                    "Например: https://example.com",
                ]),
                "ideate": header("💡", "IDEATE", [
                    "Введи тему — получишь 10 идей для постов.",
                    "Например: запуск подкаста",
                ]),
                "prompt": header("🎯", "PROMPT", [
                    "Опиши задачу — помогу составить промпт.",
                    "Например: научи LLM писать заголовки",
                ]),
            }
            set_state(user_id, payload)
            await _menu_reply(event, prompts[payload],
                              attachments=main_menu_keyboard())
            return

        if payload == "restart":
            clear_state(user_id)
            from app.max.handlers.start import build_start_text
            await _menu_reply(
                event,
                build_start_text(),
                attachments=main_menu_keyboard(),
            )
            return

    # ---------- free text while awaiting input ----------
    @dp.message_created(
        StateActionFilter(exact={"research", "copy", "plan", "analyze", "ideate", "prompt", "post", "post:awaiting"})
    )
    async def on_menu_text(event: MessageCreated) -> None:
        text = (event.message.body.text or "").strip()
        if not text:
            return
        chat_id, user_id = event.get_ids()
        st = get_state(user_id)
        if st is None:
            return  # not in a menu-flow; free_chat handler takes over
        action = st["action"]
        clear_state(user_id)

        if action == "research":
            await do_research(deps, event, text)
        elif action == "copy":
            await do_copy(deps, event, text)
        elif action == "plan":
            await do_plan(deps, event, text)
        elif action == "analyze":
            await do_analyze(deps, event, text)
        elif action == "ideate":
            await do_ideate(deps, event, text)
        elif action == "prompt":
            await do_prompt(deps, event, text)
        elif action == "post":
            await do_post(deps, event, text)
        elif action == "post:awaiting":
            # B2 (2026-08-19): тот же поток что и action=='post', но с понятным state-name.
            await do_post(deps, event, text)


async def _list_channels(deps: Deps, event: MessageCallback) -> list[str]:
    """List channels learned from MAX bot_added/bot_removed events.

    MAX removed GET /chats in June 2026. GET /subscriptions is only the
    webhook-subscription list, so it cannot discover channel membership.
    """
    try:
        items = await deps.storage.list_known_channels()
    except Exception as e:  # noqa: BLE001
        logger.warning("known channel registry read failed: %s", e)
        items = []
    if items:
        lines = [f"• {row.chat_id} — {row.title or 'Канал MAX'}" for row in items]
        lines.extend([
            "",
            "Скопируйте chat_id и выберите «Ввести chat_id вручную».",
        ])
        return lines
    return [
        "Каналы ещё не зарегистрированы.",
        "",
        "MAX больше не отдаёт готовый список каналов через API.",
        "Каталог пополняется, когда бота добавляют в канал:",
        "1. Откройте канал в MAX",
        "2. Настройки → Участники → Добавить",
        "3. Найдите @id752703975446_3_bot",
        "4. Назначьте администратором",
        "",
        "После события bot_added нажмите 📋 Мои каналы ещё раз.",
    ]


def _help_text() -> list[str]:
    return [
        "Выбери действие кнопкой меню.",
        "Для продвинутых пользователей — текстовые команды:",
        "/research, /copy, /plan, /post, /analyze, /ideate, /prompt",
        "Или просто напиши текст — бот ответит в диалоге.",
    ]
