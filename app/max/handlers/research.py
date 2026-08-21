"""/research <тема> [7d|30d|90d|all] — fallback text command (menu button is preferred).

F2.5 (2026-08-21): the user can append an optional freshness window:
  /research AI in legal           → fresh=30d (default)
  /research AI in legal 7d        → fresh=7d
  /research AI in legal 30d       → fresh=30d
  /research AI in legal 90d       → fresh=90d
  /research AI in legal all       → no date filter (historical only)

The executor (``app.max.executors.do_research``) re-parses the
freshness token, so this handler does not need to know about it — it
just passes the full text through.
"""
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
            await event.message.answer(
                "Использование: /research <тема> [7d|30d|90d|all]\n"
                "По умолчанию окно свежести — 30d.",
                attachments=home_button(),
            )
            return
        await do_research(deps, event, topic)

