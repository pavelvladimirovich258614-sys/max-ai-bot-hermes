"""Tests for Markdown formatting helpers + MarkdownSender.

We test pure-Python helpers without touching the MAX SDK:
  * `sanitise()` — drops zero-length formatting pairs, strips # headings,
    collapses blank runs, leaves well-formed Markdown alone.
  * `MarkdownSender` is exercised by a tiny fake bot (we don't hit MAX).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.max.formatting import MarkdownSender, sanitise
from app.max.executors import _safe_send
from app.config import Settings


# --- sanitise() ---


def test_sanitise_empty_and_none():
    assert sanitise("") == ""
    assert sanitise(None) == ""


def test_sanitise_keeps_well_formed_markdown():
    # Basic bold/italic/link should survive untouched.
    src = "**bold** and _italic_ and [t](u)"
    assert sanitise(src) == src


def test_sanitise_drops_zero_length_pairs():
    assert "**" not in sanitise("hello **** world")
    assert "**" not in sanitise("a ** ** b")
    assert "__" not in sanitise("a __ __ b")
    assert "~~" not in sanitise("a ~~~~ b")
    assert "==" not in sanitise("a == == b")


def test_sanitise_strips_headings_and_converts_to_bold():
    out = sanitise("# Title\n## Subhead\nbody")
    assert "#" not in out
    assert "**Title**" in out
    assert "**Subhead**" in out
    assert "body" in out


def test_sanitise_collapses_blank_runs():
    out = sanitise("a\n\n\n\n\nb")
    assert out == "a\n\nb"


def test_sanitise_does_not_break_inline_code():
    src = "Use `pip install foo` to install"
    assert sanitise(src) == src


def test_sanitise_drops_orphan_underscores_in_words():
    # LLM sometimes emits "foo_bar" inside a word; we leave that alone
    # (only zero-length pairs are stripped). This is a "no false positives"
    # guard so future maintainers don't over-strip.
    src = "variable_name_with_underscores"
    assert sanitise(src) == src


def test_sanitise_realistic_llm_output():
    src = (
        "# **Копирайтинг**\n\n"
        "## Что нужно знать\n"
        "\n"
        "Use **bold** and _italic_. Avoid `code_blocks`.\n\n"
        "Link: [example](https://example.com)\n"
        "\n\n\n\n\n"
        "--- separator ---\n"
    )
    out = sanitise(src)
    # Heading markers gone, replaced with **...**.
    assert "# " not in out
    assert "**Копирайтинг**" in out
    assert "**bold**" in out and "_italic_" in out
    assert "[example](https://example.com)" in out
    # Blank runs collapsed.
    assert "\n\n\n" not in out


# --- MarkdownSender ---


class FakeBot:
    """Captures every send_message call so we can assert what was sent."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, *, chat_id, text, attachments=None,
                           format=None, link=None, notify=None, **kwargs):
        self.calls.append({
            "chat_id": chat_id,
            "text": text,
            "attachments": attachments,
            "format": format,
            "link": link,
        })
        # Mimic SendedMessage -> .message.body.mid
        return SimpleNamespace(message=SimpleNamespace(body=SimpleNamespace(mid="m-1")))


@pytest.mark.asyncio
async def test_sender_uses_plain_text_without_format():
    bot = FakeBot()
    sender = MarkdownSender(bot)
    await sender.send(chat_id=42, text="**hi**", attachments=["kb"])
    assert len(bot.calls) == 1
    call = bot.calls[0]
    assert call["chat_id"] == 42
    assert call["text"] == "hi"
    assert call["attachments"] == ["kb"]
    assert call["format"] is None


@pytest.mark.asyncio
async def test_sender_cleans_markdown_by_default():
    bot = FakeBot()
    sender = MarkdownSender(bot)
    await sender.send(chat_id=1, text="# Heading\n- item")
    assert bot.calls[0]["text"] == "📌 Heading\n• item"


@pytest.mark.asyncio
async def test_markdown_sender_can_skip_sanitise():
    bot = FakeBot()
    sender = MarkdownSender(bot)
    await sender.send(chat_id=1, text="# Heading", sanitise_text=False)
    # Sanitise disabled → original text preserved.
    assert bot.calls[0]["text"] == "# Heading"


@pytest.mark.asyncio
async def test_markdown_sender_reply_attaches_link():
    from maxapi.enums.message_link_type import MessageLinkType

    bot = FakeBot()
    sender = MarkdownSender(bot)
    await sender.reply(
        chat_id=99, reply_to_mid="prev-mid-7",
        text="**under**", attachments=["kb"],
    )
    assert len(bot.calls) == 1
    call = bot.calls[0]
    assert call["chat_id"] == 99
    assert call["text"] == "under"
    assert call["format"] is None
    assert call["attachments"] == ["kb"]
    link = call["link"]
    assert link is not None
    assert getattr(link, "type", None) == MessageLinkType.REPLY
    assert getattr(link, "mid", None) == "prev-mid-7"


@pytest.mark.asyncio
async def test_sender_default_format_is_plain():
    bot = FakeBot()
    sender = MarkdownSender(bot)
    assert sender._format is None


@pytest.mark.asyncio
async def test_safe_send_cleans_text_even_when_setting_says_plain(monkeypatch):
    bot = FakeBot()

    async def answer(text, attachments=None):
        raise AssertionError("_safe_send must use the central plain sender")

    event = SimpleNamespace(
        bot=bot,
        message=SimpleNamespace(
            chat=SimpleNamespace(chat_id=77),
            answer=answer,
        ),
    )
    settings = Settings(_env_file=None)
    settings.message_format = "plain"
    monkeypatch.setattr("app.max.executors.config_mod.get_settings", lambda: settings)
    mid = await _safe_send(event, "**Жирный**\n- пункт")
    assert mid == "m-1"
    assert bot.calls[0]["text"] == "Жирный\n• пункт"
    assert bot.calls[0]["format"] is None


# --- send_home_button ---

from app.max.ui import send_home_button


@pytest.mark.asyncio
async def test_send_home_button_reply_anchored_to_mid():
    from maxapi.enums.message_link_type import MessageLinkType

    bot = FakeBot()
    ok = await send_home_button(bot, chat_id=77, reply_to_mid="prev-mid-99")
    assert ok is True
    assert len(bot.calls) == 1
    call = bot.calls[0]
    assert call["chat_id"] == 77
    # Reply with link attached.
    link = call["link"]
    assert link is not None
    assert getattr(link, "type", None) == MessageLinkType.REPLY
    assert getattr(link, "mid", None) == "prev-mid-99"
    # Keyboard markup attached.
    assert call["attachments"] is not None
    assert len(call["attachments"]) == 1


@pytest.mark.asyncio
async def test_send_home_button_without_reply_falls_back_to_send():
    bot = FakeBot()
    ok = await send_home_button(bot, chat_id=5, reply_to_mid=None)
    assert ok is True
    assert len(bot.calls) == 1
    call = bot.calls[0]
    assert call["chat_id"] == 5
    # No link on the fallback path.
    assert call["link"] is None
    # Still attaches the keyboard.
    assert call["attachments"] is not None


@pytest.mark.asyncio
async def test_send_home_button_swallows_errors():
    class BoomBot:
        async def send_message(self, **_):
            raise RuntimeError("kaboom")

    ok = await send_home_button(BoomBot(), chat_id=1, reply_to_mid="x")
    assert ok is False