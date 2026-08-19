"""/plan <N> <ниша> — fallback text command (menu button is preferred)."""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.handlers.deps import Deps
from app.max.executors import do_plan
from app.max.keyboards import home_button


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("plan"))
    async def cmd_plan(event: MessageCreated) -> None:
        body = (event.message.body.text or "").replace("/plan", "", 1).strip()
        if not body:
            await event.message.answer("Использование: /plan <N> <ниша>",
                                       attachments=home_button())
            return
        await do_plan(deps, event, body)

