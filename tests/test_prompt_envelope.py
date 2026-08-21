"""Tests for the rebuilt /prompt role_prompt and domain matrix (F1, 2026-08-21).

We assert:

  * ``app.llm.prompts.domains.DOMAIN_SKILL_MATRIX`` is the canonical
    source of truth (8 domains, every domain has skills + downstream +
    warn_if_missing).
  * The role_prompt in ``app.llm.prompts.prompt.SYSTEM_PROMPT`` is a
    string that references every domain, names every skill, contains
    the envelope sections from F1.3, and enforces the negative
    constraints from F1.4.
  * The legacy module ``prompt_engineer`` still exports
    ``SYSTEM_PROMPT`` (backwards-compat for ``Orchestrator``).
"""
import pytest

from app.llm.prompts.domains import (
    DOMAIN_KEYS,
    DOMAIN_SKILL_MATRIX,
    get_domain,
    has_downstream,
    list_domains,
    pick_skill,
    warn_if_missing,
)
from app.llm.prompts.prompt import SYSTEM_PROMPT

# All 8 canonical domain keys — F1.1 contract.
EXPECTED_DOMAINS = [
    "text/marketing",
    "product",
    "design",
    "code",
    "research",
    "video",
    "finance",
    "agent",
]


# ---------------------------------------------------------------------------
# Matrix tests (F1.2)
# ---------------------------------------------------------------------------


def test_domains_matrix_has_exactly_eight_domains():
    assert set(DOMAIN_KEYS) == set(EXPECTED_DOMAINS), (
        f"DOMAIN_SKILL_MATRIX must have exactly the 8 canonical domains; "
        f"got {list(DOMAIN_KEYS)}"
    )
    assert len(DOMAIN_KEYS) == 8


def test_domains_matrix_canonical_order_matches_f11():
    """The order is the one the role_prompt lists. Tests catch reorder bugs."""
    assert list(DOMAIN_KEYS) == EXPECTED_DOMAINS


def test_every_domain_has_at_least_one_skill():
    for key, entry in DOMAIN_SKILL_MATRIX.items():
        assert entry.get("skills"), f"domain {key!r} has empty skills"
        assert isinstance(entry["skills"], list)
        assert all(isinstance(s, str) and s for s in entry["skills"]), (
            f"domain {key!r} has a non-string or empty skill"
        )


def test_every_domain_has_downstream_and_warn_flag():
    for key, entry in DOMAIN_SKILL_MATRIX.items():
        assert "downstream" in entry, f"domain {key!r} missing downstream"
        assert "warn_if_missing" in entry, (
            f"domain {key!r} missing warn_if_missing flag"
        )
        assert isinstance(entry["warn_if_missing"], bool)


def test_domains_with_bot_command_mark_warn_false():
    """If the bot has /<x>, we don't need to warn the user."""
    for domain in ("text/marketing", "product", "research", "agent"):
        entry = DOMAIN_SKILL_MATRIX[domain]
        assert entry["warn_if_missing"] is False, (
            f"domain {domain!r} has a bot command, must not warn"
        )
        assert has_downstream(domain) is True


def test_domains_without_bot_command_mark_warn_true():
    """If the bot lacks /<x>, the envelope must warn the user."""
    for domain in ("design", "video", "finance", "code"):
        entry = DOMAIN_SKILL_MATRIX[domain]
        assert entry["warn_if_missing"] is True, (
            f"domain {domain!r} lacks a bot command, must warn"
        )
        # "code" downstream is "(inline)" which is still "not a button" — but
        # has_downstream() is False for "(none)" / "(inline)" / "".
        assert has_downstream(domain) is False


def test_pick_skill_returns_primary_for_known_domain():
    assert pick_skill("research") is not None
    assert "deep-research" in pick_skill("research").lower()
    assert pick_skill("code") is not None


def test_pick_skill_returns_none_for_unknown_domain():
    assert pick_skill("nonsense-domain") is None
    assert get_domain("nonsense-domain") is None


def test_list_domains_returns_canonical_order():
    assert list_domains() == list(DOMAIN_KEYS)


# ---------------------------------------------------------------------------
# Role-prompt content tests (F1.1, F1.3, F1.4)
# ---------------------------------------------------------------------------


def test_prompt_is_nonempty_string():
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT) > 2000, (
        "role_prompt suspiciously short — likely missing the envelope contract"
    )


def test_prompt_classifies_marketing_domain():
    assert "text/marketing" in SYSTEM_PROMPT
    # Russian hint for the LLM:
    assert "копирайтинг" in SYSTEM_PROMPT.lower()


def test_prompt_classifies_code_domain():
    # The role_prompt must mention both the domain key and at least one of
    # its skills. The "code" key appears in the canonical domain list
    # and in the matrix mapping line.
    assert "code" in SYSTEM_PROMPT
    assert "code-review" in SYSTEM_PROMPT
    assert "fullstack-dev" in SYSTEM_PROMPT


def test_prompt_classifies_research_domain():
    assert "research" in SYSTEM_PROMPT
    assert "deep-research" in SYSTEM_PROMPT
    assert "industry-research-report-writer" in SYSTEM_PROMPT


def test_prompt_lists_all_eight_domains():
    for domain in EXPECTED_DOMAINS:
        assert domain in SYSTEM_PROMPT, f"domain {domain!r} missing from role_prompt"


def test_prompt_asks_clarification_on_ambiguous():
    """F1.1 contract: ambiguous input -> ONE clarifying question, do not guess."""
    # The prompt must mention that ambiguous cases get a clarifying question.
    lower = SYSTEM_PROMPT.lower()
    assert "clarifying question" in lower or "уточняющий вопрос" in lower
    # And it must explicitly forbid guessing.
    assert "do not guess" in lower or "не угадывать" in lower


def test_prompt_returns_full_envelope():
    """F1.3 contract: every envelope section is named in the role_prompt."""
    required_sections = [
        "[ROLE]",
        "[CONTEXT]",
        "[GOAL]",
        "[DoD — Definition of Done]",
        "[SCOPE]",
        "[STEPS]",
        "[CONSTRAINTS]",
        "[OUTPUT FORMAT]",
        "## Мета",
        "## Eval-кейсы",
        "## Где взять результат",
    ]
    for section in required_sections:
        assert section in SYSTEM_PROMPT, f"envelope section {section!r} missing"


def test_prompt_includes_meta_block_fields():
    """The Мета block must list the four required keys: Домен, Навык, Режим, Сложность."""
    for field in ("Домен:", "Навык:", "Режим:", "Сложность:"):
        assert field in SYSTEM_PROMPT, f"meta field {field!r} missing"


def test_prompt_includes_constraints_section_values():
    """F1.3: the CONSTRAINTS section must enumerate the four value types."""
    assert "Негативные:" in SYSTEM_PROMPT
    assert "Формат:" in SYSTEM_PROMPT
    assert "Язык:" in SYSTEM_PROMPT
    assert "Размер:" in SYSTEM_PROMPT


def test_prompt_includes_eval_cases_minimum_two():
    assert "Case 1" in SYSTEM_PROMPT
    assert "Case 2" in SYSTEM_PROMPT


def test_prompt_includes_specific_verb_guidance():
    """F1.3: GOAL must use a specific verb, not vague 'improve'."""
    # "improve X" is the anti-example; "add a `retry` parameter" is the positive one.
    assert "add" in SYSTEM_PROMPT.lower()
    # The "specific verb" rule is articulated:
    assert "specific verb" in SYSTEM_PROMPT.lower() or "сильный глагол" in SYSTEM_PROMPT.lower()


def test_prompt_forbids_naked_output():
    """F1.4: 'NEVER return a naked answer' — the envelope IS the deliverable."""
    assert "NEVER" in SYSTEM_PROMPT
    # Find at least the "naked" prohibition:
    lower = SYSTEM_PROMPT.lower()
    assert "naked" in lower or "без обвязки" in lower
    # And the positive framing: the envelope IS the deliverable.
    assert "envelope" in lower and "deliverable" in lower


def test_prompt_forbids_mixing_domains():
    """F1.4: 'NEVER mix two domains in one envelope'."""
    assert "NEVER mix two domains" in SYSTEM_PROMPT or "смешивать домены" in SYSTEM_PROMPT.lower()


def test_prompt_requires_dod_no_skipping():
    """F1.4: 'NEVER skip DoD'."""
    assert "NEVER skip DoD" in SYSTEM_PROMPT or "не пропускать DoD" in SYSTEM_PROMPT.lower()


def test_prompt_forbids_trivial_eval_cases():
    """F1.4: 'input=X → output=X' is not a case."""
    assert "input=X" in SYSTEM_PROMPT or "input=\"напиши" in SYSTEM_PROMPT


def test_prompt_forbids_new_packages():
    """F1.4: NEVER add new Python packages."""
    assert "NEVER add new Python packages" in SYSTEM_PROMPT or (
        "не добавлять" in SYSTEM_PROMPT.lower() and "пакет" in SYSTEM_PROMPT.lower()
    )


def test_prompt_forbids_trading_signals_in_finance():
    """F1.4: finance is research only, no buy/sell signals."""
    lower = SYSTEM_PROMPT.lower()
    assert "торговые сигналы" in lower or "buy/sell" in lower
    assert "research only" in lower or "только research" in lower or "research-режим" in lower or "research mode" in lower


# ---------------------------------------------------------------------------
# Backwards-compat: the legacy prompt_engineer module must still work
# (Orchestrator imports from there).
# ---------------------------------------------------------------------------


def test_prompt_engineer_module_re_exports_system_prompt():
    """Orchestrator does ``from app.llm.prompts.prompt_engineer import SYSTEM_PROMPT``."""
    from app.llm.prompts import prompt_engineer
    assert hasattr(prompt_engineer, "SYSTEM_PROMPT")
    # And the re-exported prompt must be the same string the new module exports
    # (otherwise downstream tests would be confused).
    assert prompt_engineer.SYSTEM_PROMPT is SYSTEM_PROMPT


def test_prompt_skill_matrix_importable():
    """The role_prompt mentions the matrix by name; the import must work."""
    # The prompt.py module imports DOMAIN_SKILL_MATRIX for tooling-side
    # static analysis. That import must succeed at module load.
    from app.llm.prompts.prompt import DOMAIN_SKILL_MATRIX as D
    assert D is DOMAIN_SKILL_MATRIX
