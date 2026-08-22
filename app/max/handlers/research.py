"""/research <тема> [7d|30d|90d|all] — fallback text command (menu button is preferred).

F2.5 (2026-08-21): the user can append an optional freshness window:
  /research AI in legal           → fresh=30d (default)
  /research AI in legal 7d        → fresh=7d
  /research AI in legal 30d       → fresh=30d
  /research AI in legal 90d       → fresh=90d
  /research AI in legal all       → no date filter (historical only)

Sub-task D (2026-08-22): when ``MAX_USE_PIPELINE`` is on, the handler
delegates to ``PipelineOrchestrator`` (which itself uses the cached
``run_research_cached`` helper). The user-facing response keeps the
same shape as the legacy path — extra pipeline output is appended as
an optional second message so behaviour is backwards-compatible.

When ``MAX_RESEARCH_EVAL_ENABLED`` is on AND pipeline is on, the
research artefact is also passed to ``ResearchEvaluator`` and any
REVISION_REQUIRED issues are surfaced to the user as a warning.
"""
from __future__ import annotations

import logging

from maxapi import Dispatcher
from maxapi.types import Command, MessageCreated

from app.max.handlers.deps import Deps
from app.max.executors import do_research
from app.max.keyboards import home_button

logger = logging.getLogger("maxbot.handlers.research")


# ---- Sub-task D helpers (pipeline + evaluator hooks) ----


async def _enrich_via_pipeline(deps: Deps, topic: str, user_id: int):
    """Run the multi-step pipeline if ``MAX_USE_PIPELINE`` is on.

    Returns a ``PipelineContext`` on success, or ``None`` if the
    integration is disabled / failed. Never raises — failures are
    logged and we fall back to the legacy ``do_research`` path.
    """
    settings = getattr(deps, "settings", None)
    if settings is None or not getattr(settings, "max_use_pipeline", False):
        return None
    try:
        from app.core.pipeline_orchestrator import PipelineOrchestrator
        from app.core.research_cascade import ResearchCascade
        cascade = getattr(deps, "cascade", None) or ResearchCascade(settings)
        orchestrator = PipelineOrchestrator(settings=settings, cascade=cascade)
        return await orchestrator.run(topic, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("pipeline integration failed: %s", e)
        return None


async def _evaluate_artifact(deps: Deps, artifact: dict):
    """Run the LLM-backed evaluator if ``MAX_RESEARCH_EVAL_ENABLED`` is on.

    Returns an ``EvalOutput`` on success, or ``None`` if disabled /
    failed. Never raises — a missing or broken evaluator must not
    break /research.
    """
    settings = getattr(deps, "settings", None)
    if settings is None or not getattr(settings, "max_research_eval_enabled", False):
        return None
    try:
        from app.llm.evaluator import ResearchEvaluator
        evaluator = ResearchEvaluator(settings)
        return await evaluator.evaluate(artifact)
    except Exception as e:  # noqa: BLE001
        logger.warning("evaluator hook failed: %s", e)
        return None


def _format_pipeline_followup(ctx) -> str:
    """Build the optional follow-up message that the user sees after the
    legacy research response. Returns an empty string when there is
    nothing useful to say.
    """
    if ctx is None or ctx.analysis is None:
        return ""
    bits: list[str] = []
    if ctx.cascade_result:
        status = ctx.cascade_result.get("status", "?")
        bits.append(f"📊 Pipeline: cascade status={status}")
    if ctx.enrichment and ctx.enrichment.get("status") == "applied":
        bits.append("✅ Hermes enrichment applied")
    elif ctx.enrichment:
        bits.append(f"ℹ️ Hermes: {ctx.enrichment.get('status', '?')}")
    if ctx.analysis:
        bits.append("\n" + str(ctx.analysis).strip())
    return "\n".join(bits).strip()


def _format_evaluator_warning(eval_output) -> str:
    """Build the user-facing warning text from an EvalOutput."""
    if eval_output is None or eval_output.status != "REVISION_REQUIRED":
        return ""
    if not eval_output.required_changes:
        return ""
    changes = eval_output.required_changes[:3]
    text = "⚠️ Evaluator требует доработки:\n" + "\n".join(f"  • {c}" for c in changes)
    return text


# ---- handler registration ----


async def cmd_research(deps: Deps, event: MessageCreated) -> None:
    """Module-level handler so tests can call it directly.

    Registered with the Dispatcher in ``register()`` below.
    """
    topic = (event.message.body.text or "").replace("/research", "", 1).strip()
    if not topic:
        await event.message.answer(
            "Использование: /research <тема> [7d|30d|90d|all]\n"
            "По умолчанию окно свежести — 30d.",
            attachments=home_button(),
        )
        return

    # Sub-task D: run the pipeline first when enabled. The pipeline
    # produces a self-contained PipelineContext with both cascade
    # output and (optional) Hermes enrichment. We use it as the
    # primary response; the legacy do_research path stays as the
    # fallback when pipeline is off or fails.
    chat_id, user_id = event.get_ids() if hasattr(event, "get_ids") else (0, 0)
    ctx = await _enrich_via_pipeline(deps, topic, user_id)

    if ctx is not None and ctx.cascade_result is not None:
        # Pipeline succeeded — send a user-facing summary built from
        # the cascade output (key_findings) + pipeline analysis.
        cascade = ctx.cascade_result
        findings = cascade.get("key_findings", [])
        top = findings[:5]
        bullets = "\n".join(
            f"• {f.get('claim', '?')[:120]} ({f.get('url', '')})"
            for f in top
        ) if top else "(нет находок)"
        user_msg = (
            f"🔍 Pipeline research: <{topic}>\n"
            f"Статус: {cascade.get('status', '?')}, "
            f"находок: {len(findings)}\n\n"
            f"{bullets}"
        )
        await event.message.answer(user_msg, attachments=home_button())

        # Pipeline follow-up (enrichment status, analysis)
        followup = _format_pipeline_followup(ctx)
        if followup:
            await event.message.answer(followup, attachments=home_button())

        # Evaluator hook (only if pipeline succeeded and eval is on)
        if ctx.cascade_result:
            eval_out = await _evaluate_artifact(deps, ctx.cascade_result)
            warning = _format_evaluator_warning(eval_out)
            if warning:
                await event.message.answer(warning, attachments=home_button())
        return

    # Legacy path (no pipeline integration or pipeline failed)
    await do_research(deps, event, topic)


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_created(Command("research"))
    async def _cmd_research(event: MessageCreated) -> None:
        await cmd_research(deps, event)
