"""/start and /help handlers (text fallback). /start now shows the main menu."""
from __future__ import annotations

from pathlib import Path

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.descriptions import START_TOUR
from app.max.handlers.deps import Deps
from app.max.keyboards import main_menu_keyboard
from app.max.ui import attach_local_image, clean_for_max

MENU_BANNER = Path(__file__).resolve().parents[3] / "assets" / "menu_banner.png"


def build_start_text() -> str:
    greeting = (
        "👋 Привет! Я MAX AI Bot — твой оркестратор Wu-Tang Hermes.\n"
        "Маркетинг, аналитика, контент, стратегия и публикации в каналы.\n"
    )
    return clean_for_max(greeting + "\n" + START_TOUR)


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("start"))
    async def cmd_start(event: MessageCreated) -> None:
        chat_id, user_id = event.get_ids()
        await deps.storage.upsert_user(
            user_id,
            username=getattr(event.message.sender, "username", None)
            if event.message.sender else None,
        )
        text = build_start_text()
        image_atts = await attach_local_image(deps.bot, MENU_BANNER)
        await event.message.answer(
            text,
            attachments=image_atts + main_menu_keyboard(),
        )

    @dp.message_created(Command("help"))
    async def cmd_help(event: MessageCreated) -> None:
        # Help stays available as a fallback but is not advertised in the menu flow.
        await event.message.answer(
            "🤖 MAX AI Bot — оркестратор Wu-Tang Hermes.\n\n"
            "Текстовые команды (fallback):\n"
            "/research <тема> — глубокий бриф с источниками\n"
            "/copy <тема> [стиль] — пост-копирайтинг\n"
            "/plan <N> <ниша> — контент-план на N дней\n"
            "/post <channel_id> <текст> — черновик поста с approve-кнопкой\n"
            "/analyze <URL> — резюме страницы по ссылке\n"
            "/ideate <тема> — 10 идей для постов\n"
            "/prompt <задача> — структура промпта и антипаттерны\n"
            "/status — диагностика (Hermes / LLM / последние ошибки)\n\n"
            "Или просто напиши любой текст — бот ответит в диалоге.",
            attachments=main_menu_keyboard(),
        )