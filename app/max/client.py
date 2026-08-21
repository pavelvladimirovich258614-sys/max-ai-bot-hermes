"""MAX client: build the CompliantBot + Dispatcher, register handlers, set commands.

This module wires together:
  * CompliantBot (token via Authorization header, current API domain)
  * Dispatcher with all command / callback / free-chat handlers
  * Bot command menu via SetCommands (POST /commands) — Pavel: set_my_commands
    uses PATCH /me which is deprecated (support ends 15.09.2026).
"""
from __future__ import annotations

import logging

from maxapi import Bot, Dispatcher
from maxapi.types import BotCommand

from app.config import Settings
from app.context import get_context
from app.max.bot_wrapper import CompliantBot
from app.max.handlers import (
    analyze,
    callback_handler,
    channel_registry,
    copy,
    free_chat,
    hermes_button,
    ideate,
    image_gen,
    menu,
    plan,
    post,
    prompt_cmd,
    research,
    start,
    status,
)
from app.max.publisher import Publisher
from app.middleware.auth import AuthGate

logger = logging.getLogger("maxbot.max.client")

# 12 команд: видны при вводе "/" в MAX.
BOT_COMMANDS = [
    BotCommand(name="start", description="🚀 Запустить бота"),
    BotCommand(name="help", description="❓ Справка по командам"),
    BotCommand(name="research", description="🔍 Глубокий research с источниками"),
    BotCommand(name="copy", description="✍️ 3 варианта продающего поста"),
    BotCommand(name="plan", description="📅 Контент-план на N дней"),
    BotCommand(name="post", description="📤 Черновик поста → публикация в канал"),
    BotCommand(name="analyze", description="🔬 Анализ ссылки (URL)"),
    BotCommand(name="ideate", description="💡 10 идей для постов"),
    BotCommand(name="prompt", description="🎯 Помощь с промптом"),
    BotCommand(name="image", description="🎨 Сгенерировать картинку"),
    BotCommand(name="status", description="📊 Статус бота (Hermes / LLM)"),
    BotCommand(name="restart", description="🔄 Перезапустить бота"),
]


def build_bot(settings: Settings) -> CompliantBot:
    return CompliantBot(
        token=settings.max_bot_token,
        api_base=settings.max_api_base,
    )


def register_handlers(dp: Dispatcher, deps: "object") -> None:
    channel_registry.register(dp, deps)
    start.register(dp, deps)
    menu.register(dp, deps)
    research.register(dp, deps)
    copy.register(dp, deps)
    plan.register(dp, deps)
    post.register(dp, deps)
    analyze.register(dp, deps)
    ideate.register(dp, deps)
    prompt_cmd.register(dp, deps)
    free_chat.register(dp, deps)
    callback_handler.register(dp, deps)
    image_gen.register(dp, deps)
    status.register(dp, deps)
    # Hermes button (Feature V3) — registered LAST so it gets a shot at
    # every callback/text the others ignored.
    hermes_button.register(dp, deps)


async def setup_bot() -> tuple[CompliantBot, Dispatcher]:
    """Create bot + dispatcher, register handlers, push the command menu."""
    ctx = get_context()
    s = ctx.settings
    bot = build_bot(s)
    dp = Dispatcher()

    publisher = Publisher(bot, ctx.storage)
    auth = AuthGate(s)
    # HermesDispatcher (Feature V3, 2026-08-19) — один на бот, держит
    # активные сессии в памяти. Передаём в Deps ниже.
    from app.hermes.dispatcher import HermesDispatcher
    hermes = HermesDispatcher(bot, ctx.storage, s)
    ctx.hermes = hermes
    from app.max.handlers.deps import Deps

    deps = Deps(
        bot=bot,
        dp=dp,
        orchestrator=ctx.orchestrator,
        storage=ctx.storage,
        publisher=publisher,
        auth=auth,
        hermes=hermes,
    )
    register_handlers(dp, deps)

    # B1 (2026-08-19): используем set_commands() — НЕ set_my_commands().
    # set_my_commands() отправляет PATCH /me, поддержка прекращается
    # 15.09.2026. set_commands() идёт через POST /commands (или
    # эквивалент) — это правильный путь для MAX.
    try:
        await bot.set_commands(*BOT_COMMANDS)
        logger.info(
            "Slash-commands registered: %d commands (%s)",
            len(BOT_COMMANDS),
            ", ".join(c.name for c in BOT_COMMANDS),
        )
    except Exception as e:  # noqa: BLE001
        # Если новый API не сработал — фолбэк на deprecated set_my_commands,
        # чтобы хоть что-то было зарегистрировано.
        logger.warning("set_commands() failed (%s) — fallback to set_my_commands", e)
        try:
            await bot.set_my_commands(*BOT_COMMANDS)
            logger.info("Bot commands registered (deprecated PATCH /me)")
        except Exception as e2:  # noqa: BLE001
            logger.error("Both set_commands and set_my_commands failed: %s / %s", e, e2)

    return bot, dp
