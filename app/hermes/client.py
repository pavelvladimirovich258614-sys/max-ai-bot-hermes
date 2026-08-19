"""Client to the Wu-Tang Hermes orchestrator (RZA).

Two transport modes, selected by HERMES_MODE (auto = try HTTP, fall back to CLI):
  * http : POST {HERMES_RZA_URL} with {"role", "task", "context"}
  * cli  : `hermes peer dm rza "<task>"` as a subprocess

If Hermes is unavailable, callers should fall back to the direct LLM
(see app.core.orchestrator.Orchestrator).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger("maxbot.hermes")

ROLE_TO_HERMES = {
    "researcher": "GZA",
    "copywriter": "Cappadonna",
    "marketer": "Cappadonna",
    "ideator": "Cappadonna",
    "analyzer": "GZA",
    "prompt_engineer": "Masta Killa",
    "chat": "RZA",
}


class HermesClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._mode = settings.hermes_mode
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    def resolve_mode(self) -> str:
        """Determine the effective mode for this environment."""
        if self._mode in ("http", "cli", "none"):
            return self._mode
        # auto: probe HTTP, fall back to cli
        return "http"

    async def route(
        self,
        role: str,
        task: str,
        context: Optional[dict] = None,
        timeout: float = 110.0,
    ) -> Optional[str]:
        """Send a task to RZA. Returns the text answer, or None if unreachable."""
        mode = self.resolve_mode()
        if mode == "none":
            return None
        if mode == "http":
            text = await self._route_http(role, task, context, timeout)
            if text is not None:
                return text
            # HTTP failed; if auto, try cli once
            if self._mode == "auto":
                logger.info("Hermes HTTP failed; trying CLI")
                return await self._route_cli(role, task, timeout)
            return None
        if mode == "cli":
            return await self._route_cli(role, task, timeout)
        return None

    async def _route_http(
        self, role: str, task: str, context: Optional[dict], timeout: float
    ) -> Optional[str]:
        try:
            resp = await self._http.post(
                self._s.hermes_rza_url,
                json={"role": role, "task": task, "context": context or {}},
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                # Accept either {"answer": ...} or {"result": ...} or raw string
                if isinstance(data, dict):
                    return data.get("answer") or data.get("result") or data.get("text")
                return str(data)
            logger.warning("Hermes HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("Hermes HTTP error: %s", e)
            return None

    async def _route_cli(self, role: str, task: str, timeout: float) -> Optional[str]:
        try:
            cmd = f'{self._s.hermes_rza_cli} "{task}"'
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            if proc.returncode == 0:
                return out.decode("utf-8", errors="replace").strip()
            logger.warning("Hermes CLI rc=%s err=%s", proc.returncode, err[:200])
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("Hermes CLI error: %s", e)
            return None
