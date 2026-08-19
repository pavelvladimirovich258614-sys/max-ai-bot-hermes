"""MAX bot wrapper.

IMPORTANT (compliance with MAX hard rules, 18.08.2026):
  * The upstream `maxapi` SDK defaults `API_URL` to `https://botapi.max.ru`,
    which is the DEPRECATED/forbidden domain. We override it to the current
    `https://platform-api2.max.ru`.
  * The upstream SDK sends the token as a query parameter (`?access_token=`),
    which is forbidden. `CompliantBot` removes it from the query and instead
    sends it as the `Authorization: <token>` header on every request.

Everything else (polling, webhook, dispatcher, keyboards) is standard maxapi.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from maxapi import Bot

logger = logging.getLogger("maxbot.max")


class CompliantBot(Bot):
    # Override the forbidden default domain with the current one.
    API_URL = "https://platform-api2.max.ru"

    def __init__(self, token: str, api_base: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(token, **kwargs)
        self._auth_token = token
        if api_base:
            # Allow per-deployment override (must be platform-api2.max.ru).
            self.API_URL = api_base.rstrip("/")
        # Strip the token from query params; we deliver it via the header.
        self.params.pop("access_token", None)

    async def request(
        self,
        method: Any,
        path: Any,
        model: Any = None,
        is_return_raw: bool = False,
        **kwargs: Any,
    ) -> Any:
        params = dict(kwargs.get("params") or {})
        # Belt-and-suspenders: never leak the token into the query string.
        params.pop("access_token", None)
        headers = dict(kwargs.get("headers") or {})
        if self._auth_token:
            headers.setdefault("Authorization", self._auth_token)
        kwargs["params"] = params
        kwargs["headers"] = headers
        return await super().request(
            method, path, model=model, is_return_raw=is_return_raw, **kwargs
        )
