from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.max.handlers import channel_registry


class _Deps:
    def __init__(self):
        self.storage = AsyncMock()
        self.bot = AsyncMock()


@pytest.mark.asyncio
async def test_bot_added_persists_channel_title():
    deps = _Deps()
    event = SimpleNamespace(
        chat_id=-1001,
        is_channel=True,
        chat=SimpleNamespace(title="Канал эксперта"),
    )
    await channel_registry.record_bot_added(deps, event)
    deps.storage.upsert_known_chat.assert_awaited_once_with(
        chat_id=-1001,
        title="Канал эксперта",
        is_channel=True,
        active=True,
    )


@pytest.mark.asyncio
async def test_bot_added_persists_group_without_marking_channel():
    deps = _Deps()
    event = SimpleNamespace(
        chat_id=-2001,
        is_channel=False,
        chat=SimpleNamespace(title="Рабочая группа"),
    )
    await channel_registry.record_bot_added(deps, event)
    assert deps.storage.upsert_known_chat.await_args.kwargs["is_channel"] is False


@pytest.mark.asyncio
async def test_bot_removed_deactivates_known_chat():
    deps = _Deps()
    event = SimpleNamespace(chat_id=-1001)
    await channel_registry.record_bot_removed(deps, event)
    deps.storage.set_known_chat_active.assert_awaited_once_with(-1001, False)


@pytest.mark.asyncio
async def test_channel_activity_middleware_registers_channel_from_edited_event():
    deps = _Deps()
    deps.bot.get_chat_by_id.return_value = SimpleNamespace(title="Новый канал")
    event = SimpleNamespace(
        message=SimpleNamespace(
            recipient=SimpleNamespace(chat_id=-72143469522347, chat_type="channel")
        )
    )
    next_handler = AsyncMock()
    middleware = channel_registry.ChannelActivityMiddleware(deps)

    await middleware(next_handler, event, {"source": "test"})

    deps.bot.get_chat_by_id.assert_awaited_once_with(-72143469522347)
    deps.storage.upsert_known_chat.assert_awaited_once_with(
        chat_id=-72143469522347,
        title="Новый канал",
        is_channel=True,
        active=True,
    )
    next_handler.assert_awaited_once_with(event, {"source": "test"})


@pytest.mark.asyncio
async def test_channel_activity_middleware_ignores_non_channel_message():
    deps = _Deps()
    event = SimpleNamespace(
        message=SimpleNamespace(
            recipient=SimpleNamespace(chat_id=-2001, chat_type="chat")
        )
    )
    next_handler = AsyncMock()
    middleware = channel_registry.ChannelActivityMiddleware(deps)

    await middleware(next_handler, event, {})

    deps.bot.get_chat_by_id.assert_not_awaited()
    deps.storage.upsert_known_chat.assert_not_awaited()
    next_handler.assert_awaited_once_with(event, {})


@pytest.mark.asyncio
async def test_my_channels_reads_local_registry_not_webhook_subscriptions():
    from app.max.handlers.menu import _list_channels

    deps = _Deps()
    deps.storage.list_known_channels.return_value = [
        SimpleNamespace(chat_id=-1001, title="Канал эксперта")
    ]
    lines = await _list_channels(deps, SimpleNamespace())
    assert lines[0] == "• -1001 — Канал эксперта"
    deps.storage.list_known_channels.assert_awaited_once()
    deps.bot.get_subscriptions.assert_not_called()
