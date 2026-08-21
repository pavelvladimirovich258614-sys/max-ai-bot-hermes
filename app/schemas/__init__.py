"""Pydantic schemas package (F2, 2026-08-21).

Schemas are shared between the live /research pipeline, the smoke CLI,
and the test suite. They live in their own package (not under
``app.llm`` or ``app.max``) because both the LLM-emitted text and the
hand-built test fixtures have to parse into the same shape.
"""
