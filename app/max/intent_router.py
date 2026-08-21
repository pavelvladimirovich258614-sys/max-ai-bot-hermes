"""Free-text intent router (F0.3, 2026-08-21).

When the user sends a free-text message (no slash command, no active menu
state) the bot previously routed it straight to ``role="chat"``. With
Hermes RZA unavailable and the LLM fallback also down, the user got
"Сервис временно недоступен" and no path forward.

This module adds a tiny keyword-based dispatcher: the first matching intent
wins, and we set the corresponding FSM state (same as a menu button click)
so the next user message runs the proper executor (do_research, do_copy, …).

If nothing matches, the router returns ``None`` and the caller falls back to
the regular chat role. To prevent the router from stealing every message,
we only fire it on inputs of at least 3 words (short greetings like
"привет" or "спасибо" stay in free chat).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Each rule maps a free-text intent to the menu-flow state the user is in
# right after pressing the matching button. ``keywords`` are matched as
# whole words (case-insensitive, Russian + English), not as substrings,
# to avoid false positives like "аналитика" matching "analyze".
#
# Rules are tried in order; the FIRST match wins. We keep the more specific
# rules first (URL → analyze, "опубликуй" → post) so they beat the more
# general catch-alls (research / copy / ideate).
INTENT_RULES: list[dict] = [
    {
        "name": "analyze",
        "command_payload": "analyze",
        "next_state": "analyze",
        "keywords": [
            r"\b(разбери ссылк|разбери страниц|разбери сайт|разбери url|что по ссылк|что на сайт|анализ статьи|analyze url|разбор url|разбор статьи|разбор страницы|по этой ссылке|по ссылке)\b",
            r"https?://\S+",  # any URL → analyze intent
        ],
    },
    {
        "name": "post",
        "command_payload": "post",
        "next_state": "post:awaiting",
        "keywords": [
            r"\b(опубликуй|опубликовать|пост в канал|отправь в канал|publish|размести в канал|выложи в канал)\b",
        ],
    },
    {
        "name": "research",
        "command_payload": "research",
        "next_state": "research",
        "keywords": [
            r"\b(ищи|искать|найди|изучи|изучить|тема|темы|факты|источник|источники|research|investigate|статья о|разбери тему|разложи тему)\b",
        ],
    },
    {
        "name": "copy",
        "command_payload": "copy",
        "next_state": "copy",
        "keywords": [
            r"\b(напиши|пиши|сочини|придумай пост|копирайт|copywrite|копирайтер|вариант поста|рекламный текст|напиши текст)\b",
        ],
    },
    {
        "name": "plan",
        "command_payload": "plan",
        "next_state": "plan",
        "keywords": [
            r"\b(план|расписание|неделя|месяц|контент-план|content plan|календарь|calendar|рубрик)\w*",
        ],
    },
    {
        "name": "ideate",
        "command_payload": "ideate",
        "next_state": "ideate",
        "keywords": [
            r"\b(идеи|идея|придумай|темы для постов|topics|идеи для контента|рубрик\w* идей|10 идей)\b",
        ],
    },
    {
        "name": "prompt",
        "command_payload": "prompt",
        "next_state": "prompt",
        "keywords": [
            r"\b(промпт|усиль промпт|перепиши промпт|улучши промпт|помоги с промптом|prompt engineering)\b",
        ],
    },
]


@dataclass(frozen=True)
class IntentMatch:
    """Result of routing a free-text message to a menu-flow state."""

    name: str
    command_payload: str
    next_state: str
    matched_keyword: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command_payload": self.command_payload,
            "next_state": self.next_state,
            "matched_keyword": self.matched_keyword,
        }


def _word_count(text: str) -> int:
    """Whitespace-separated word count, robust to double spaces / newlines."""
    return len([w for w in text.split() if w])


def route_intent(text: str) -> Optional[IntentMatch]:
    """Return the first matching intent, or None for "leave in free chat".

    Rules are tried in the order they appear in ``INTENT_RULES``; first match
    wins. We only run the matcher on inputs with >= 3 words to avoid
    stealing short greetings / thanks / yes-no replies.
    """
    if not text or not text.strip():
        return None
    if _word_count(text) < 3:
        return None
    lower = text.lower()
    for rule in INTENT_RULES:
        for pattern in rule["keywords"]:
            m = re.search(pattern, lower, flags=re.IGNORECASE | re.UNICODE)
            if m:
                return IntentMatch(
                    name=rule["name"],
                    command_payload=rule["command_payload"],
                    next_state=rule["next_state"],
                    matched_keyword=m.group(0),
                )
    return None
