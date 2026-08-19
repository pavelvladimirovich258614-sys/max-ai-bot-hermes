"""Tiny in-memory FSM: maps user_id -> {action, data}.

No library — just a module-level dict. Used to remember what the user is
expected to send next after clicking a menu button (e.g. "awaiting research
topic"). Cleared after the input is consumed.
"""
from __future__ import annotations

from typing import Optional

_user_states: dict[int, dict] = {}


def set_state(user_id: int, action: str, data: Optional[dict] = None) -> None:
    """Store one flat FSM record.

    Handlers read flow fields directly (``state.get('mode')``), so keeping
    extras under a nested ``data`` key breaks image generation. A flat record
    is the canonical schema: ``{'action': ..., **data}``.
    """
    _user_states[user_id] = {"action": action, **(data or {})}


def get_state(user_id: int) -> Optional[dict]:
    return _user_states.get(user_id)


def clear_state(user_id: int) -> None:
    _user_states.pop(user_id, None)
