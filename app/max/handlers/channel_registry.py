"""Keep the local channel registry in sync with MAX lifecycle and activity events."""
from __future__ import annotations

import logging
from typing import Any

from maxapi import Dispatcher
from maxapi.filters.middleware import BaseMiddleware
from maxapi.types import BotAdded, BotRemoved

from app.max.handlers.deps import Deps

logger = logging.getLogger("maxbot.channel_registry")


def _event_title(event: Any) -> str:
    chat = getattr(event, "chat", None)
    return str(
        getattr(chat, "title", None)
        or getattr(chat, "name", None)
        or ("Канал MAX" if getattr(event, "is_channel", False) else "Группа MAX")
    )


def _recipient_channel_id(event: Any) -> int | None:
    """Return a channel id only when MAX explicitly labels recipient as channel."""
    message = getattr(event, "message", None)
    recipient = getattr(message, "recipient", None)
    chat_id = getattr(recipient, "chat_id", None)
    chat_type = getattr(recipient, "chat_type", None)
    kind = getattr(chat_type, "value", chat_type)
    if chat_id is None or str(kind).lower() != "channel":
        return None
    return int(chat_id)


class ChannelActivityMiddleware(BaseMiddleware):
    """Observe every update without consuming it and discover channel recipients.

    MAX can omit a historical ``bot_added`` update from long-polling. Any later
    channel activity still carries ``recipient.chat_type == 'channel'``; fetch
    its title once and persist it without interfering with command handlers.
    """

    def __init__(self, deps: Deps) -> None:
        self._deps = deps
        self._seen_chat_ids: set[int] = set()

    async def __call__(self, handler, event_object: Any, data: dict[str, Any]) -> Any:
        chat_id = _recipient_channel_id(event_object)
        if chat_id is not None and chat_id not in self._seen_chat_ids:
            try:
                chat = await self._deps.bot.get_chat_by_id(chat_id)
                title = str(getattr(chat, "title", None) or "Канал MAX")
                await self._deps.storage.upsert_known_chat(
                    chat_id=chat_id,
                    title=title,
                    is_channel=True,
                    active=True,
                )
                self._seen_chat_ids.add(chat_id)
                logger.info("known channel recovered from activity chat_id=%s title=%s", chat_id, title)
            except Exception as exc:  # noqa: BLE001
                logger.warning("channel activity discovery failed chat_id=%s: %s", chat_id, exc)
        return await handler(event_object, data)


async def record_bot_added(deps: Deps, event: BotAdded) -> None:
    await deps.storage.upsert_known_chat(
        chat_id=event.chat_id,
        title=_event_title(event),
        is_channel=bool(event.is_channel),
        active=True,
    )
    logger.info(
        "known chat registered chat_id=%s is_channel=%s title=%s",
        event.chat_id,
        event.is_channel,
        _event_title(event),
    )


async def record_bot_removed(deps: Deps, event: BotRemoved) -> None:
    await deps.storage.set_known_chat_active(event.chat_id, False)
    logger.info("known chat deactivated chat_id=%s", event.chat_id)


def register(dp: Dispatcher, deps: Deps) -> None:
    # Must be outer middleware: maxapi executes only the first matching
    # handler for an event, while this observer must never swallow commands.
    dp.register_outer_middleware(ChannelActivityMiddleware(deps))

    @dp.bot_added()
    async def on_bot_added(event: BotAdded) -> None:
        await record_bot_added(deps, event)

    @dp.bot_removed()
    async def on_bot_removed(event: BotRemoved) -> None:
        await record_bot_removed(deps, event)
