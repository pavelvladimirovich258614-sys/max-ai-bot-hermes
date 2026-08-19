"""Application context: holds singletons shared across the app.

This avoids module-level globals and makes the code testable. `init_context`
is called once from the FastAPI lifespan (and from tests with overrides).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.config import Settings, get_settings
from app.core.orchestrator import Orchestrator
from app.db.storage import Storage
from app.llm.client import LLMClient


@dataclass
class AppContext:
    settings: Settings
    storage: Storage
    llm: LLMClient
    orchestrator: Orchestrator
    # maxapi bot / Hermes dispatcher, set during startup (optional in tests)
    bot: Optional[object] = None
    hermes: Optional[object] = None


_CTX: Optional[AppContext] = None


def set_context(ctx: AppContext) -> None:
    global _CTX
    _CTX = ctx


def get_context() -> AppContext:
    if _CTX is None:
        raise RuntimeError("AppContext not initialised. Call init_context() first.")
    return _CTX


async def init_context(settings: Optional[Settings] = None) -> AppContext:
    """Build all singletons and run DB migrations."""
    settings = settings or get_settings()
    storage = Storage(settings.db_path)
    await storage.init()
    llm = LLMClient(settings)
    orchestrator = Orchestrator(settings, llm, storage)
    ctx = AppContext(
        settings=settings,
        storage=storage,
        llm=llm,
        orchestrator=orchestrator,
    )
    set_context(ctx)
    return ctx
