"""/prompt <задача> — fallback text command (menu button is preferred)."""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.handlers.deps import Deps
from app.max.executors import do_prompt
from app.max.keyboards import home_button


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("prompt"))
    async def cmd_prompt(event: MessageCreated) -> None:
        task = (event.message.body.text or "").replace("/prompt", "", 1).strip()
        if not task:
            await event.message.answer("Использование: /prompt <задача>",
                                       attachments=home_button())
            return
        await do_prompt(deps, event, task)

