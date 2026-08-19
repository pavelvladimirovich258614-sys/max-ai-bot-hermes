"""/copy <тема> [стиль] — fallback text command (menu button is preferred)."""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.handlers.deps import Deps
from app.max.executors import do_copy
from app.max.keyboards import home_button


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("copy"))
    async def cmd_copy(event: MessageCreated) -> None:
        body = (event.message.body.text or "").replace("/copy", "", 1).strip()
        if not body:
            await event.message.answer("Использование: /copy <тема> [стиль]",
                                       attachments=home_button())
            return
        await do_copy(deps, event, body)

