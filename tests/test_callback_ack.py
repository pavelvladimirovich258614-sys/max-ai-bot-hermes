from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.max.handlers.hermes_button import _ack as hermes_ack
from app.max.handlers.image_gen import _menu_ack as image_ack
from app.max.handlers.menu import _ack_callback as menu_ack


def _event():
    return SimpleNamespace(
        answer=AsyncMock(),
        bot=SimpleNamespace(send_callback=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_menu_callback_ack_does_not_create_chat_message():
    event = _event()
    await menu_ack(event)
    event.answer.assert_awaited_once_with(notification="Готово")
    event.bot.send_callback.assert_not_called()


@pytest.mark.asyncio
async def test_image_callback_ack_does_not_create_chat_message():
    event = _event()
    await image_ack(event)
    event.answer.assert_awaited_once_with(notification="Готово")
    event.bot.send_callback.assert_not_called()


@pytest.mark.asyncio
async def test_hermes_callback_ack_does_not_create_chat_message():
    event = _event()
    await hermes_ack(event)
    event.answer.assert_awaited_once_with(notification="Готово")
    event.bot.send_callback.assert_not_called()
