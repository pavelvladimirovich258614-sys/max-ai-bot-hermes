"""Unit tests for the free-text intent router (F0.3, 2026-08-21).

We assert three contracts:

  1. The router maps well-formed Russian free-text intents to the
     right menu state (e.g. "напиши пост про ..." → ``copy``).
  2. The router does NOT match on short messages (greetings / thanks).
  3. A URL on its own routes to ``analyze`` so users can paste a link
     and get the analyzer instead of the chat LLM.
"""
from app.max.intent_router import route_intent


def test_route_research_russian_verb():
    m = route_intent("Разбери тему про AI-юристов в 2026")
    assert m is not None
    assert m.name == "research"
    assert m.next_state == "research"
    assert m.command_payload == "research"


def test_route_copy_russian_verb():
    m = route_intent("Напиши пост про найм юриста")
    assert m is not None
    assert m.name == "copy"
    assert m.next_state == "copy"


def test_route_plan_russian_phrase():
    m = route_intent("Сделай контент-план на 14 дней для коуча")
    assert m is not None
    assert m.name == "plan"
    assert m.next_state == "plan"


def test_route_ideate_russian_phrase():
    m = route_intent("Придумай 10 идей для блога психолога")
    assert m is not None
    assert m.name == "ideate"
    assert m.next_state == "ideate"


def test_route_prompt_russian_phrase():
    m = route_intent("Усиль мой промпт для заголовков блога")
    assert m is not None
    assert m.name == "prompt"
    assert m.next_state == "prompt"


def test_route_post_russian_phrase():
    m = route_intent("Опубликуй этот пост в канал MAX")
    assert m is not None
    assert m.name == "post"
    assert m.next_state == "post:awaiting"


def test_route_analyze_for_url():
    m = route_intent("Посмотри https://example.com/article про налоги")
    assert m is not None
    assert m.name == "analyze"
    assert m.next_state == "analyze"


def test_route_analyze_for_explicit_link_phrase():
    m = route_intent("Разбери ссылку https://habr.com/ru/article/123")
    assert m is not None
    assert m.name == "analyze"


def test_short_messages_do_not_route():
    # Two words or fewer → router stays silent, free chat consumes them.
    for short in ("привет", "спасибо", "ок", "ладно", "хорошо", "ok thanks"):
        m = route_intent(short)
        assert m is None, f"router matched a short message: {short!r}"


def test_three_words_or_more_qualifies():
    # "привет как дела" — 3 words but no intent keyword → still None.
    m = route_intent("привет как дела")
    assert m is None


def test_unrelated_three_word_message_does_not_route():
    # Three words, but none of the router's keywords.
    m = route_intent("расскажи про себя")
    assert m is None


def test_empty_and_whitespace_only():
    assert route_intent("") is None
    assert route_intent("   \n\t  ") is None
    assert route_intent(None or "") is None


def test_router_first_match_wins():
    # "напиши идеи" contains BOTH copy ("напиши") and ideate ("идеи")
    # keywords. The router must pick the first rule that matches (copy).
    m = route_intent("Напиши идеи для блога")
    assert m is not None
    assert m.name == "copy"


def test_router_is_case_insensitive():
    m1 = route_intent("НАПИШИ пост про ИИ")
    m2 = route_intent("напиши Пост про ИИ")
    assert m1 is not None and m2 is not None
    assert m1.name == m2.name == "copy"
