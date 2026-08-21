"""Domain → skill matrix (F1.2, 2026-08-21).

The ``/prompt`` command is the entry point of Pavel's "Context Engineering"
workflow. It must:

  1. Classify the user's task into ONE of the 8 supported domains.
  2. Map that domain to a primary skill (and a backup).
  3. Suggest a downstream agent / command that can execute the resulting
     prompt envelope.

The matrix here is the canonical source of truth. The role_prompt in
``prompt.py`` references it by name (``DOMAIN_SKILL_MATRIX``) so the LLM
sees the same data we use in tests.

Why Python (not hard-coded in the prompt)?
  * The matrix changes as Pavel adds / removes skills. Putting it in code
    means a single edit + a test run, no re-prompting required.
  * The role_prompt stays short: just the keys (so the LLM knows the
    categories) + a directive to read the matrix.

Schema (per domain)::

    {
        "name":        "<human-readable domain name in Russian>",
        "skills":      [<primary skill>, <backup skill>, ...],
        "downstream":  "<command or '(none)' if the user runs the prompt inline>",
        "warn_if_missing": True | False,  # warn when the downstream agent is
                                           # not available in the current bot
    }

Domains with ``downstream == "(none)"`` and ``warn_if_missing == True``
will trigger a warning when the role_prompt produces the envelope: the
envelope tells the user which skill should be loaded, but the bot itself
does not provide a one-click button for that skill.
"""
from __future__ import annotations

from typing import Optional


# Ordered to match the role_prompt's classification list (F1.1).
DOMAIN_SKILL_MATRIX: dict[str, dict] = {
    "text/marketing": {
        "name": "Text / Marketing",
        "description": "Копирайтинг, посты, слоганы, лендинг-тексты, email-цепочки.",
        "skills": [
            "Proof-driven Copywriter (Hermes skill)",
            "humanizer (Hermes skill)",
        ],
        "downstream": "/copy",
        "downstream_label": "Команда /copy — обернёт результат в 3 варианта поста.",
        "warn_if_missing": False,
    },
    "product": {
        "name": "Product",
        "description": "PRD, RFC, позиционирование, roadmap, user stories.",
        "skills": [
            "PRD Assistant (interview-me + prd-assistant)",
            "planning-and-task-breakdown",
        ],
        "downstream": "/plan",
        "downstream_label": "Команда /plan — превратит контент-план в roadmap.",
        "warn_if_missing": False,
    },
    "design": {
        "name": "Design (UI/UX)",
        "description": "UI/UX, лендинги, баннеры, иконки, дизайн-системы.",
        "skills": [
            "frontend-design",
            "ui-ux-pro-max",
            "landing-page-builder",
        ],
        "downstream": "(none)",
        "downstream_label": (
            "В этом боте нет /design. Передай обвязку в Claude Code "
            "или в skill `frontend-design` напрямую."
        ),
        "warn_if_missing": True,
    },
    "code": {
        "name": "Code",
        "description": "Фичи, рефакторинг, баги, тесты, код-ревью.",
        "skills": [
            "code-review",
            "fullstack-dev",
            "test-driven-development",
            "systematic-debugging",
        ],
        "downstream": "(inline)",
        "downstream_label": (
            "Обвязка исполняется inline (копипаст в IDE / sub-agent). "
            "В боте нет команды для кода."
        ),
        "warn_if_missing": True,
    },
    "research": {
        "name": "Research",
        "description": "Разбор темы, конкуренты, тренды, due diligence.",
        "skills": [
            "deep-research",
            "industry-research-report-writer",
            "arxiv (academic)",
        ],
        "downstream": "/research",
        "downstream_label": "Команда /research — выполнит бриф с источниками.",
        "warn_if_missing": False,
    },
    "video": {
        "name": "Video",
        "description": "Сценарии, shorts, раскадровка, монтажные заметки.",
        "skills": [
            "video-story-generator",
            "video-motion-analysis",
            "ai-video-creator",
        ],
        "downstream": "(none)",
        "downstream_label": (
            "В этом боте нет /video. Передай обвязку в skill "
            "`video-story-generator`."
        ),
        "warn_if_missing": True,
    },
    "finance": {
        "name": "Finance (research only)",
        "description": (
            "Research по активам и секторам. НЕ торговые сигналы, "
            "НЕ 'купи X завтра'."
        ),
        "skills": [
            "ai-trading-consortium (research mode)",
            "hedge-fund-expert-team (research mode)",
        ],
        "downstream": "(none)",
        "downstream_label": (
            "В этом боте нет /finance и это намеренно — бот не даёт "
            "торговых сигналов. Обвязка для внешнего research-агента."
        ),
        "warn_if_missing": True,
    },
    "agent": {
        "name": "Agent / Planning",
        "description": (
            "Карьерные решения, планирование, рефлексия, "
            "личные стратегии."
        ),
        "skills": [
            "ceo-assistant",
            "future-mirror",
            "interview-me",
        ],
        "downstream": "/plan",
        "downstream_label": "Команда /plan + /hermes — для длинных сценариев.",
        "warn_if_missing": False,
    },
}


# Domain keys in the canonical order — used by the role_prompt when it
# asks the LLM to pick one. Tests assert that this list is in lock-step
# with ``DOMAIN_SKILL_MATRIX.keys()``.
DOMAIN_KEYS: tuple[str, ...] = tuple(DOMAIN_SKILL_MATRIX.keys())


def list_domains() -> list[str]:
    """Return the canonical list of domain keys."""
    return list(DOMAIN_KEYS)


def get_domain(domain: str) -> Optional[dict]:
    """Return the matrix entry for a domain, or None if unknown."""
    return DOMAIN_SKILL_MATRIX.get(domain)


def pick_skill(domain: str) -> Optional[str]:
    """Return the primary skill name for a domain, or None if unknown."""
    entry = get_domain(domain)
    if not entry or not entry.get("skills"):
        return None
    return entry["skills"][0]


def has_downstream(domain: str) -> bool:
    """True if the bot has a one-click command for this domain's envelope."""
    entry = get_domain(domain)
    if not entry:
        return False
    downstream = entry.get("downstream", "(none)")
    return downstream not in ("(none)", "(inline)", "")


def warn_if_missing(domain: str) -> bool:
    """True if the envelope should warn the user that the downstream is missing."""
    entry = get_domain(domain)
    return bool(entry and entry.get("warn_if_missing", False))
