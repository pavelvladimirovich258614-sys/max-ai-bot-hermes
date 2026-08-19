"""Unit tests for ProgressReporter — no real MAX API required.

We use a tiny fake event/bot that records every call. The reporter must:
  1. Send the intro on enter.
  2. Capture the message_id of the intro.
  3. Edit the same message (not send a new one) on subsequent steps, gated
     by the rate-limit interval.
  4. Fall back to send_message if edit_message raises.
  5. flush() bypasses the cooldown.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.max.ui import ProgressReporter


class FakeBody:
    def __init__(self, mid: str) -> None:
        self.mid = mid


class FakeMessage:
    def __init__(self, mid: str) -> None:
        self.body = FakeBody(mid)
        self.chat = SimpleNamespace(chat_id=42)
        self._sent: list[tuple[str, str]] = []  # (mid, text) of answer()s
        self._next_mid = 0

    async def answer(self, text=None, **_kw):
        self._next_mid += 1
        mid = f"ans-{self._next_mid}"
        self._sent.append((mid, text or ""))
        return FakeSent(mid)


class FakeSent:
    def __init__(self, mid: str) -> None:
        self.message = FakeMessage(mid)


class FakeBot:
    def __init__(self, fail_edit: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []  # (kind, text)
        self.edits: list[tuple[str, str]] = []  # (message_id, text)
        self._next_mid = 0
        self.fail_edit = fail_edit

    def _new_mid(self) -> str:
        self._next_mid += 1
        return f"m{self._next_mid}"

    async def send_message(self, chat_id, text, **_kw):
        mid = self._new_mid()
        self.sent.append((mid, text))
        return FakeSent(mid)

    async def edit_message(self, message_id, text, **_kw):
        if self.fail_edit:
            raise RuntimeError("simulated edit failure")
        self.edits.append((message_id, text))
        return SimpleNamespace(message=FakeMessage(message_id))


def make_event(bot: FakeBot) -> SimpleNamespace:
    return SimpleNamespace(
        message=FakeMessage("incoming-1"),
        bot=bot,
    )


@pytest.mark.asyncio
async def test_intro_is_sent_and_captured():
    bot = FakeBot()
    event = make_event(bot)
    async with ProgressReporter(event, "⏳ Обрабатываю…") as prog:
        assert prog.message_id == "ans-1"
        # The intro was sent via event.message.answer(), not bot.send_message.
        assert event.message._sent and event.message._sent[0][1] == "⏳ Обрабатываю…"
        assert bot.edits == []
        assert bot.sent == []


@pytest.mark.asyncio
async def test_rapid_steps_are_rate_limited_then_edited():
    bot = FakeBot()
    event = make_event(bot)
    async with ProgressReporter(event, "⏳ Обрабатываю…", min_interval=0.05) as prog:
        intro_mid = prog.message_id
        # All within 50ms: only the very first step() is allowed to edit.
        for line in ("🤖 Думаю…", "🔎 Ищу источники…", "📊 Структурирую…"):
            await prog.step(line)
        # Wait out the cooldown, then one more step should fire.
        await asyncio.sleep(0.08)
        await prog.step("✅ Готово…")
    # We expect at least 1 edit; the intro is a send, not an edit.
    assert len(bot.edits) >= 1
    last_mid, last_text = bot.edits[-1]
    assert last_mid == intro_mid
    assert "⏳ Обрабатываю…" in last_text
    assert "✅ Готово…" in last_text


@pytest.mark.asyncio
async def test_flush_bypasses_cooldown():
    bot = FakeBot()
    event = make_event(bot)
    async with ProgressReporter(event, "⏳ …", min_interval=10.0) as prog:
        await prog.step("🤖 Думаю…")
        # Cooldown is 10s, so the previous step was rate-limited (no edit).
        assert bot.edits == []
        await prog.flush()
        # flush() should have forced an edit.
        assert len(bot.edits) == 1


@pytest.mark.asyncio
async def test_edit_failure_falls_back_to_send():
    bot = FakeBot(fail_edit=True)
    event = make_event(bot)
    async with ProgressReporter(event, "⏳ …", min_interval=0.0) as prog:
        # Two step() calls, both should attempt to edit, both fail, both
        # fall back to bot.send_message.
        await prog.step("🤖 Думаю…")
        await prog.step("✅ Готово…")
    # No edits succeeded.
    assert bot.edits == []
    # The intro is on event.message; both fallbacks are on bot.send_message.
    # So 2 fallback sends, each carrying the cumulative body.
    assert len(bot.sent) == 2
    final_text = bot.sent[-1][1]
    assert "⏳ …" in final_text
    assert "🤖 Думаю…" in final_text
    assert "✅ Готово…" in final_text
