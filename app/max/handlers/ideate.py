"""/ideate <тема> — fallback text command (menu button is preferred)."""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.handlers.deps import Deps
from app.max.executors import do_ideate
from app.max.keyboards import home_button


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("ideate"))
    async def cmd_ideate(event: MessageCreated) -> None:
        topic = (event.message.body.text or "").replace("/ideate", "", 1).strip()
        if not topic:
            await event.message.answer("Использование: /ideate <тема>",
                                       attachments=home_button())
            return
        await do_ideate(deps, event, topic)

