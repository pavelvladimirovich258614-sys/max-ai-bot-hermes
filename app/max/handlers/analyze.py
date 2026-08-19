"""/analyze <URL> — fallback text command (menu button is preferred)."""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.handlers.deps import Deps
from app.max.executors import do_analyze
from app.max.keyboards import home_button


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("analyze"))
    async def cmd_analyze(event: MessageCreated) -> None:
        url = (event.message.body.text or "").replace("/analyze", "", 1).strip()
        if not url:
            await event.message.answer("Использование: /analyze <URL>",
                                       attachments=home_button())
            return
        await do_analyze(deps, event, url)

