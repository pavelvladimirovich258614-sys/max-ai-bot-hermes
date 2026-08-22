"""Multi-step research pipeline orchestrator (Batch 3 / Sub-task A, 2026-08-22).

This sits *above* the F2 cascade. When the cascade alone is not enough
(<3 findings, or PARTIAL, or the topic looks like a comparison /
analysis), we ask Hermes peer RZA for an enrichment pass and
synthesize the two outputs into a final analysis string.

Design rules:
  - F2 cascade is the source of truth for "what the world says".
  - Hermes enrichment is best-effort and gracefully degrades.
  - One cascade call. No retries. The FSM is explicit and acyclic.
  - Hermes subprocess is spawned via ``create_subprocess_exec`` (NOT
    shell=True) to avoid injection via the topic string.
  - Default 60s timeout on Hermes subprocess.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from app.config import Settings
from app.core.pipeline_state import (
    PipelineContext,
    PipelineState,
    can_transition,
    is_terminal,
)

if TYPE_CHECKING:
    from app.core.research_cascade import ResearchCascade

logger = logging.getLogger("maxbot.pipeline")

# ---- tunables (kept here so tests can override) ----

DEFAULT_HERMES_TIMEOUT_S = 60.0
HERMES_BIN_ENV = "HERMES_CLI_BIN"

# Keywords that signal a "complex" topic needing extra context.
# Kept conservative тАФ these are common B2B/analyst requests.
_COMPLEX_KEYWORDS = (
    "compare", "analyze", "vs", "versus",
    "╨░╨╜╨░╨╗╨╕╨╖", "╤Б╤А╨░╨▓╨╜╨╕", "╤Б╤А╨░╨▓╨╜╨╡╨╜╨╕╨╡", "╤А╨░╨╖╨▒╨╛╤А", "╨╛╨▒╨╖╨╛╤А",
)

# If cascade produced fewer than this many findings, run enrichment.
_FINDINGS_THRESHOLD = 3


def should_enrich(ctx: PipelineContext) -> bool:
    """Decide whether to call Hermes for enrichment.

    Returns True when:
      - the cascade produced fewer than 3 findings, OR
      - the topic contains a complex-analysis keyword, OR
      - cascade status is PARTIAL.
    """
    if ctx.cascade_result is None:
        return False
    findings = ctx.cascade_result.get("key_findings") or []
    if len(findings) < _FINDINGS_THRESHOLD:
        return True
    topic_lower = (ctx.topic or "").lower()
    if any(kw in topic_lower for kw in _COMPLEX_KEYWORDS):
        return True
    if ctx.cascade_result.get("status") == "PARTIAL":
        return True
    return False


class PipelineOrchestrator:
    """Drive the multi-step research FSM."""

    def __init__(
        self,
        settings: Settings,
        cascade: "ResearchCascade",
        hermes_bin: Optional[str] = None,
        hermes_timeout_s: float = DEFAULT_HERMES_TIMEOUT_S,
    ) -> None:
        self._settings = settings
        self._cascade = cascade
        self._hermes_bin = hermes_bin or os.environ.get(HERMES_BIN_ENV, "hermes")
        self._hermes_timeout_s = float(hermes_timeout_s)

    # ---- public entry point ----

    async def run(self, topic: str, user_id: int = 0) -> PipelineContext:
        """Run the full FSM. Always returns a PipelineContext (even on failure)."""
        ctx = PipelineContext(topic=topic, user_id=user_id)
        try:
            ctx.transition(PipelineState.RESEARCHING)
            ctx.cascade_result = await self._run_cascade(topic)

            if should_enrich(ctx):
                ctx.transition(PipelineState.ENRICHING)
                ctx.enrichment = await self._hermes_enrich(topic)
            else:
                ctx.enrichment = {
                    "status": "skipped",
                    "reason": "not_needed",
                    "at": datetime.now(timezone.utc).isoformat(),
                }

            ctx.transition(PipelineState.ANALYZING)
            ctx.analysis = self._synthesize(ctx)
            ctx.transition(PipelineState.DONE)
        except Exception as e:  # noqa: BLE001
            logger.exception("pipeline crashed: %s", e)
            ctx.fail(f"{type(e).__name__}: {e}")
        return ctx

    async def cancel(self, ctx: PipelineContext) -> None:
        """User-initiated abort. No-op if already terminal."""
        if is_terminal(ctx.state):
            return
        if can_transition(ctx.state, PipelineState.CANCELLED):
            ctx.transition(PipelineState.CANCELLED)

    def status(self, ctx: PipelineContext) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the FSM."""
        return ctx.to_dict()

    # ---- internals ----

    async def _run_cascade(self, topic: str) -> dict[str, Any]:
        """Run the F2 cascade and return its compact JSON as a dict."""
        result = await self._cascade.run(topic, "30d")
        return json.loads(result.to_compact_json())

    async def _hermes_enrich(self, topic: str) -> dict[str, Any]:
        """Spawn ``hermes peer dm rza`` in a subprocess. Never block forever.

        Returns one of:
          ``{"status": "applied", "data": ...}``     -- subprocess OK, JSON parsed
          ``{"status": "skipped", "reason": "..."}``  -- bin missing, timeout, or rc != 0
          ``{"status": "failed",  "reason": "..."}``  -- subprocess OK but bad JSON
        """
        if not self._hermes_bin_available():
            return {"status": "skipped", "reason": "hermes_bin_missing"}

        cmd = [self._hermes_bin, "peer", "dm", "rza", topic,
               "--timeout", f"{int(self._hermes_timeout_s)}s"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return {"status": "skipped", "reason": "hermes_bin_missing"}
        except Exception as e:  # noqa: BLE001
            return {"status": "failed", "reason": f"spawn_failed: {type(e).__name__}: {e}"}

        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._hermes_timeout_s,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # noqa: BLE001
                pass
            return {"status": "skipped", "reason": "timeout"}

        if proc.returncode != 0:
            return {
                "status": "skipped",
                "reason": f"rc={proc.returncode}",
            }

        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return {"status": "skipped", "reason": "empty_stdout"}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Maybe a warning line was printed first; try last JSON-looking line.
            for line in reversed(raw.splitlines()):
                line = line.strip()
                if line.startswith(("{", "[")):
                    try:
                        data = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            else:
                return {"status": "failed", "reason": "invalid_json"}
        return {"status": "applied", "data": data}

    def _synthesize(self, ctx: PipelineContext) -> str:
        """Produce a final human-readable analysis from cascade + enrichment."""
        parts: list[str] = []
        if ctx.cascade_result:
            answer = (ctx.cascade_result.get("answer") or "").strip()
            if answer:
                parts.append(answer)
        if ctx.enrichment and ctx.enrichment.get("status") == "applied":
            data = ctx.enrichment.get("data")
            if isinstance(data, dict):
                text = data.get("answer") or data.get("text") or data.get("summary")
            else:
                text = str(data)
            if text:
                parts.append("\n[hermes]: " + str(text).strip())
        if not parts:
            parts.append(f"╨Я╨╛ ╤В╨╡╨╝╨╡ ┬л{ctx.topic}┬╗ ╨┤╨░╨╜╨╜╤Л╤Е ╨╜╨╡ ╨┐╨╛╨╗╤Г╤З╨╡╨╜╨╛.")
        return "\n".join(parts).strip()

    def _hermes_bin_available(self) -> bool:
        """True if the hermes CLI is on PATH (or HERMES_CLI_BIN is set)."""
        bin_path = self._hermes_bin
        if os.path.isabs(bin_path):
            return os.path.exists(bin_path)
        return shutil.which(bin_path) is not None
