"""Backwards-compatible re-export of the /prompt system prompt.

The role_prompt for ``/prompt`` was rewritten in F1 (2026-08-21) and
lives in ``app.llm.prompts.prompt``. The original module
``app.llm.prompts.prompt_engineer`` is kept so that:

  * ``app.core.orchestrator.Orchestrator`` can keep loading
    ``SYSTEM_PROMPT`` from the same module path
    (``app.llm.prompts.prompt_engineer``).
  * Any pre-F1 tests that imported ``SYSTEM_PROMPT`` from this module
    continue to work without modification.

If you are writing NEW code, prefer the canonical location:

    from app.llm.prompts.prompt import SYSTEM_PROMPT
    from app.llm.prompts.domains import DOMAIN_SKILL_MATRIX
"""
from app.llm.prompts.prompt import SYSTEM_PROMPT

__all__ = ["SYSTEM_PROMPT"]
