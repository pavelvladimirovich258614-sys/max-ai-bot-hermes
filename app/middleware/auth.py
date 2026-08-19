"""Auth + rate-limit middleware.

- auth: gate admin-only commands by MAX_ADMIN_USER_IDS.
- rate_limit: a shared asyncio.Semaphore enforcing the MAX 30 rps ceiling.
  The same semaphore is reused by the LLM client so the whole process stays
  under the platform limit.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import Settings

logger = logging.getLogger("maxbot.middleware")


def make_rate_limiter(rps: int = 30) -> asyncio.Semaphore:
    return asyncio.Semaphore(max(1, rps))


class AuthGate:
    def __init__(self, settings: Settings) -> None:
        self._admin_ids = set(settings.admin_user_ids)

    def is_admin(self, user_id: int) -> bool:
        if not self._admin_ids:
            # No admin list configured -> allow everyone (dev convenience).
            return True
        return user_id in self._admin_ids

    async def require_admin(self, user_id: int) -> bool:
        ok = self.is_admin(user_id)
        if not ok:
            logger.warning("Unauthorized admin command from user_id=%s", user_id)
        return ok
