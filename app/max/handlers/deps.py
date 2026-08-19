"""Handler dependencies shared by all command handlers."""
from __future__ import annotations

from dataclasses import dataclass

from maxapi import Bot, Dispatcher

from app.core.orchestrator import Orchestrator
from app.db.storage import Storage
from app.hermes.dispatcher import HermesDispatcher
from app.max.publisher import Publisher
from app.middleware.auth import AuthGate


@dataclass
class Deps:
    bot: Bot
    dp: Dispatcher
    orchestrator: Orchestrator
    storage: Storage
    publisher: Publisher
    auth: AuthGate
    # Hermes dispatcher (Feature V3, 2026-08-19). Optional so old
    # call-sites that build Deps without it don't crash — handlers that
    # need it will fall back to a friendly error message.
    hermes: "HermesDispatcher | None" = None
