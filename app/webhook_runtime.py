"""Fast acknowledgement, de-duplication and lifecycle for MAX webhooks."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("maxbot.webhook")


def update_key(payload: dict[str, Any]) -> str:
    """Return a stable identity for a MAX update across webhook retries."""
    for field in ("update_id", "event_id"):
        value = payload.get(field)
        if value is not None:
            return f"{field}:{value}"

    callback = payload.get("callback") or {}
    callback_id = callback.get("callback_id")
    if callback_id:
        return f"callback:{callback_id}"

    message = payload.get("message") or {}
    body = message.get("body") or {}
    mid = body.get("mid")
    if mid:
        return f"message:{mid}"

    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


class WebhookTaskSupervisor:
    """Accept updates immediately and process each stable update id once."""

    def __init__(self, *, ttl_s: float = 600.0, max_seen: int = 4096) -> None:
        self._ttl_s = ttl_s
        self._max_seen = max_seen
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()

    def submit(
        self,
        payload: dict[str, Any],
        process: Callable[[], Awaitable[None]],
    ) -> bool:
        """Schedule one update without awaiting it; return False for a retry."""
        now = time.monotonic()
        self._prune(now)
        key = update_key(payload)
        if key in self._seen:
            logger.info("duplicate webhook ignored key=%s", key)
            return False

        self._seen[key] = now
        self._seen.move_to_end(key)
        while len(self._seen) > self._max_seen:
            self._seen.popitem(last=False)

        task = asyncio.create_task(process(), name=f"max-webhook:{key[:80]}")
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return True

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl_s
        while self._seen:
            _, created = next(iter(self._seen.items()))
            if created >= cutoff:
                break
            self._seen.popitem(last=False)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:  # noqa: BLE001
            logger.exception("background webhook processing failed")

    async def aclose(self, timeout_s: float = 30.0) -> None:
        """Drain active updates on shutdown, then cancel any stragglers."""
        if not self._tasks:
            return
        done, pending = await asyncio.wait(self._tasks, timeout=timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.difference_update(done)
        self._tasks.difference_update(pending)
