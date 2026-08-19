"""/post <channel_id> <текст> — fallback text command (menu button is preferred)."""
from __future__ import annotations

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.handlers.deps import Deps
from app.max.executors import do_post


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("post"))
    async def cmd_post(event: MessageCreated) -> None:
        body = (event.message.body.text or "").replace("/post", "", 1).strip()
        await do_post(deps, event, body)
