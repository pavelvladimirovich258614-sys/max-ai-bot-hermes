"""Pipeline FSM states and context for the multi-step research orchestrator.

States (Batch 3 / Sub-task A):

    INIT          -- orchestrator just received a topic
    RESEARCHING   -- F2 cascade is running
    ENRICHING     -- Hermes peer RZA is being consulted (background)
    ANALYZING     -- synthesizing cascade + enrichment
    DONE          -- terminal success
    FAILED        -- terminal error (reason in ctx.errors)
    CANCELLED     -- terminal user-initiated abort

Transitions are explicit and one-way. Any state can transition to FAILED
or CANCELLED. There is no automatic retry тАФ F2 cascade is called once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class PipelineState(str, Enum):
    """FSM states for the pipeline orchestrator."""

    INIT = "INIT"
    RESEARCHING = "RESEARCHING"
    ENRICHING = "ENRICHING"
    ANALYZING = "ANALYZING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ---- transition table (F2.4-style allow-list) ----

_ALLOWED_TRANSITIONS: dict[PipelineState, set[PipelineState]] = {
    PipelineState.INIT: {PipelineState.RESEARCHING, PipelineState.FAILED, PipelineState.CANCELLED},
    PipelineState.RESEARCHING: {PipelineState.ENRICHING, PipelineState.ANALYZING,
                                PipelineState.DONE, PipelineState.FAILED, PipelineState.CANCELLED},
    PipelineState.ENRICHING: {PipelineState.ANALYZING, PipelineState.DONE,
                              PipelineState.FAILED, PipelineState.CANCELLED},
    PipelineState.ANALYZING: {PipelineState.DONE, PipelineState.FAILED, PipelineState.CANCELLED},
    PipelineState.DONE: set(),
    PipelineState.FAILED: set(),
    PipelineState.CANCELLED: set(),
}


def can_transition(src: PipelineState, dst: PipelineState) -> bool:
    """Return True if `src -> dst` is a legal transition."""
    return dst in _ALLOWED_TRANSITIONS.get(src, set())


def is_terminal(state: PipelineState) -> bool:
    """True if the state is terminal (DONE / FAILED / CANCELLED)."""
    return state in (PipelineState.DONE, PipelineState.FAILED, PipelineState.CANCELLED)


# ---- context (the data the orchestrator accumulates as it walks the FSM) ----


@dataclass
class PipelineContext:
    """Mutable context object passed through the FSM.

    Attributes:
        topic:       user-supplied research topic
        user_id:     MAX chat id (or 0 if not bound)
        state:       current FSM state
        cascade_result:  full ResearchResult.to_compact_json() output, or None
        enrichment:  dict from hermes peer RZA, or {"status": "skipped", "reason": "..."}
        analysis:    synthesized text (from cascade + enrichment), or None
        errors:      append-only list of error strings
        started_at:  ISO-8601 UTC timestamp
        completed_at: ISO-8601 UTC timestamp (set on terminal)
    """

    topic: str
    user_id: int = 0
    state: PipelineState = PipelineState.INIT
    cascade_result: Optional[dict[str, Any]] = None
    enrichment: Optional[dict[str, Any]] = None
    analysis: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    completed_at: Optional[str] = None

    def transition(self, dst: PipelineState) -> None:
        """Move to ``dst`` if legal. Raises ValueError otherwise.

        Also stamps ``completed_at`` when entering a terminal state.
        """
        if not can_transition(self.state, dst):
            raise ValueError(
                f"Illegal transition: {self.state.value} -> {dst.value}"
            )
        self.state = dst
        if is_terminal(dst):
            self.completed_at = datetime.now(timezone.utc).isoformat()

    def fail(self, reason: str) -> None:
        """Convenience: record error and move to FAILED."""
        self.errors.append(reason)
        if not is_terminal(self.state):
            self.transition(PipelineState.FAILED)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the context for logging / handoff."""
        return {
            "topic": self.topic,
            "user_id": self.user_id,
            "state": self.state.value,
            "cascade_result": self.cascade_result,
            "enrichment": self.enrichment,
            "analysis": self.analysis,
            "errors": list(self.errors),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
