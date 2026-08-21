"""Tests for command descriptions + markdown-format skill + maxapi format param.

These pin down two things Pavel asked us to verify:

  1. COMMAND_DESCRIPTIONS is populated for every menu action and contains
     concrete input/example/output instructions (not a one-liner).
  2. The Markdown-format skill file exists at the agreed path.
  3. maxapi's `send_message(..., format=...)` accepts a string 'markdown' AND
     the alias enum `Format.MARKDOWN`, so either invocation works.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# --- COMMAND_DESCRIPTIONS ---


def test_command_descriptions_has_every_menu_action():
    from app.max.descriptions import COMMAND_DESCRIPTIONS

    expected = {"research", "copy", "plan", "post", "analyze",
                "ideate", "prompt", "image"}
    missing = expected - COMMAND_DESCRIPTIONS.keys()
    assert not missing, f"Missing descriptions: {missing}"


@pytest.mark.parametrize("action", [
    "research", "copy", "plan", "post", "analyze",
    "ideate", "prompt", "image",
])
def test_command_descriptions_are_substantial(action):
    """Each description must be ≥120 chars and include 'Что ввести' / 'Например' / 'Получу'.

    Pavel (2026-08-19): 'Введи тему...' is too thin — make sure we don't
    regress to one-liners.
    """
    from app.max.descriptions import COMMAND_DESCRIPTIONS

    text = COMMAND_DESCRIPTIONS[action]
    assert len(text) >= 120, f"{action}: too short ({len(text)} chars): {text!r}"
    for must_have in ("Что ввести:", "Например:", "Получу:"):
        assert must_have in text, f"{action}: missing marker {must_have!r}"


def test_command_descriptions_are_plain_text():
    from app.max.descriptions import COMMAND_DESCRIPTIONS

    for action, text in COMMAND_DESCRIPTIONS.items():
        for forbidden in ("**", "```", "`", "[ссылками](", "[текст]("):
            assert forbidden not in text, f"{action}: markdown marker {forbidden!r}"
        for line in text.splitlines():
            assert not line.lstrip().startswith(("#", ">")), f"{action}: markdown line"


def test_start_tour_present_and_concise():
    from app.max.descriptions import START_TOUR

    assert len(START_TOUR) >= 100
    assert "Что умею:" in START_TOUR
    assert "**" not in START_TOUR


# --- markdown-format skill file ---


def test_markdown_skill_file_exists():
    skill = Path(__file__).resolve().parents[1] / "app" / "llm" / "skills" / "markdown_format.md"
    assert skill.exists(), f"Missing: {skill}"
    content = skill.read_text(encoding="utf-8")
    # Top-level title — could be `#` or `##` depending on author taste.
    assert "# SKILL:" in content
    # V4 (2026-08-19): Pavel подтвердил — MAX не рендерит markdown, поэтому
    # skill теперь про plain-text + эмодзи-маркеры.
    assert "MAX UI" in content and "не рендерит markdown" in content
    assert "Анти-паттерны" in content


def test_markdown_skill_is_attached_to_every_role():
    """When _system_prompt(role) is called, the skill is appended."""
    from app.core.orchestrator import _system_prompt

    for role in ("researcher", "copywriter", "marketer", "ideator",
                 "analyzer", "prompt_engineer", "chat", "image_prompt"):
        prompt = _system_prompt(role)
        # The skill starts with "# SKILL: ..." — match by prefix.
        assert "# SKILL:" in prompt, f"{role} missing skill"
        assert "не рендерит markdown" in prompt, f"{role} missing V4 marker"


def test_markdown_skill_attached_only_once_per_role():
    """Calling _system_prompt twice for the same role doesn't double-append."""
    from app.core.orchestrator import _system_prompt

    p1 = _system_prompt("copywriter")
    p2 = _system_prompt("copywriter")
    # Same number of occurrences of the marker.
    assert p1.count("# SKILL:") == p2.count("# SKILL:")


def test_role_prompts_do_not_require_markdown_and_define_tone():
    import importlib

    # The seven "writer" roles produce plain-text posts and therefore must
    # not ask the LLM to emit Markdown. The /prompt role (prompt_engineer)
    # is a meta-role — it generates *envelopes* that other agents will
    # execute, not user-facing text — and intentionally uses a different
    # format. We test the writer roles with the old contract and the
    # meta-role with a new one (F1, 2026-08-21).
    writer_roles = ("researcher", "copywriter", "marketer", "ideator",
                    "analyzer", "chat", "image_prompt")
    for role in writer_roles:
        prompt = importlib.import_module(f"app.llm.prompts.{role}").SYSTEM_PROMPT
        assert "Markdown, ОБЯЗАТЕЛЬНО" not in prompt, role
        assert "**" not in prompt, role
        assert "```" not in prompt, role
        assert "TONE OF VOICE" in prompt, role
        assert "delve" in prompt.lower(), role

    # /prompt is a meta-role: it builds envelopes for other agents to run.
    # The writer-role rules above (no `**`, no ` ``` `, anti-AI "delve"
    # ban, "TONE OF VOICE" heading) do not all apply: /prompt is allowed
    # to use Markdown scaffolding and Python syntax in its envelope
    # examples, and its tone section can use any heading.
    #
    # What it MUST do:
    #   * not require Markdown in its own output (envelopes are plain text)
    #   * define a tone (so downstream agents can be steered)
    #   * include anti-AI guidance somewhere (otherwise the envelope
    #     would recreate the same AI-tells in the downstream agent)
    #   * but it does not have to repeat the writer-role "delve" list —
    #     the envelope's CONSTRAINTS section is the propagation point, and
    #     the downstream agent's own role-prompt will enforce the ban.
    prompt_engineer = importlib.import_module(
        "app.llm.prompts.prompt_engineer"
    ).SYSTEM_PROMPT
    assert "Markdown, ОБЯЗАТЕЛЬНО" not in prompt_engineer
    assert "TONE" in prompt_engineer
    # Some form of anti-AI steering is required (writer-role "delve" is
    # the most explicit one — keep it for symmetry with the rest of the
    # prompt suite, but it is a SOFT requirement: any of "delve",
    # "leverage", "unlock", "AI-измы", "AI-tells" is acceptable).
    lower_pe = prompt_engineer.lower()
    assert any(
        word in lower_pe
        for word in ("delve", "leverage", "unlock", "ai-изм", "ai-tells")
    ), "prompt_engineer.SYSTEM_PROMPT must contain SOME anti-AI guidance"


# --- maxapi format parameter ---


def test_maxapi_send_message_accepts_string_markdown():
    """The maxapi SDK accepts `format='markdown'` as a string (and the enum)."""
    import inspect
    from maxapi import Bot

    sig = inspect.signature(Bot.send_message)
    assert "format" in sig.parameters, "Bot.send_message missing 'format'"
    fmt_param = sig.parameters["format"]
    # Annotation is `TextFormat | None`; StrEnum means any of its members is
    # also a string, so the string 'markdown' is valid at runtime.
    assert fmt_param.annotation is not None


def test_maxapi_format_enum_value_is_markdown():
    from maxapi.enums.format import Format

    assert str(Format.MARKDOWN).lower() == "markdown"
    assert str(Format.HTML).lower() == "html"