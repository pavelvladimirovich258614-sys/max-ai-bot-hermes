"""Core orchestration: routing MAX requests to Hermes or direct LLM.

The /research pipeline lives in ``app.core.research_cascade`` (F2, 2026-08-21).
The orchestrator and the /research handler both depend on it; it owns
the search → crawl → verify → enrich pipeline.
"""
