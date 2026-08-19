"""/research <тема> — fallback text command (menu button is preferred)."""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.handlers.deps import Deps
from app.max.executors import do_research
from app.max.keyboards import home_button


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("research"))
    async def cmd_research(event: MessageCreated) -> None:
        topic = (event.message.body.text or "").replace("/research", "", 1).strip()
        if not topic:
            await event.message.answer("Использование: /research <тема>",
                                       attachments=home_button())
            return
        await do_research(deps, event, topic)

