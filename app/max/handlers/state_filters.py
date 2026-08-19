"""State-aware filters for MAX message routing.

maxapi calls only the first matching handler for an update. Generic text
handlers therefore need explicit state filters rather than returning early
inside the function, otherwise they swallow a specialist flow.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from maxapi.filters import BaseFilter

from app.max.state import get_state


class StateActionFilter(BaseFilter):
    """Match a message only when the user's app-level FSM action matches."""

    def __init__(
        self,
        *,
        exact: Iterable[str] = (),
        prefixes: Iterable[str] = (),
    ) -> None:
        self._exact = frozenset(exact)
        self._prefixes = tuple(prefixes)

    async def __call__(self, event: Any) -> bool:
        try:
            _chat_id, user_id = event.get_ids()
        except (AttributeError, TypeError):
            return False
        if user_id is None:
            return False
        state = get_state(user_id)
        action = state.get("action") if state else None
        if not action:
            return False
        return action in self._exact or action.startswith(self._prefixes)


class NoActiveStateFilter(BaseFilter):
    """Match free chat only when no app-level FSM flow owns the text."""

    async def __call__(self, event: Any) -> bool:
        try:
            _chat_id, user_id = event.get_ids()
        except (AttributeError, TypeError):
            return False
        return user_id is not None and get_state(user_id) is None
