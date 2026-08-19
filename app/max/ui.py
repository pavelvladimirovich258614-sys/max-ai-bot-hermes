"""UI helpers for the MAX bot: rich plain-text headers, sanitisation,
chunking, ProgressReporter, and the reply-style "send home button" helper.

MAX officially supports Markdown (parameter `format=Format.MARKDOWN`); by
default we send LLM output as Markdown. When `MESSAGE_FORMAT=plain` we fall
back to the legacy plain-text-with-emoji look (see `app.max.formatting`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from maxapi import Bot
from maxapi.enums.attachment import AttachmentType
from maxapi.enums.upload_type import UploadType
from maxapi.types.input_media import InputMediaBuffer

from app.max.formatting import MarkdownSender

logger = logging.getLogger("maxbot.ui")

# MAX rejects messages where len(text) >= 4000. Stay one under to be safe.
MAX_MESSAGE_LIMIT = 3999

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITAL_RE = re.compile(r"__(.+?)__")
_STAR_RE = re.compile(r"\*([^*\n]+)\*")
_UND_RE = re.compile(r"_([^_\n]+)_")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_QUOTE_RE = re.compile(r"^\s*>\s?")
_BULLET_RE = re.compile(r"^(\s*)[*\-+]\s+")


def header(emoji: str, title: str, lines: list[str] | None = None) -> str:
    """Build a rich "header" block for a menu prompt or a result banner.

    MAX renders plain text only, so the structure is:

        🔍 RESEARCH

        <line 1>
        <line 2>

    * ``emoji``  — a single leading emoji (e.g. "🔍").
    * ``title``  — shown in UPPERCASE (no markdown syntax).
    * ``lines``  — optional body lines (already formatted by the caller).
    """
    parts = [f"{emoji} {title.strip().upper()}"]
    if lines:
        parts.append("")
        parts.extend(str(line) for line in lines)
    return "\n".join(parts)


def clean_for_max(text: str) -> str:
    """Strip markdown syntax, keep a readable, structured plain-text layout.

    * Headings (``# Title``) -> ``📌 Title``
    * Bold/italic/code markers removed (``**x**`` -> ``x``)
    * List bullets (``-``/``*``/``+``) -> ``• ``
    * Links keep label + URL (``[t](u)`` -> ``t (u)``)
    * Blockquotes lose the ``>`` marker
    * Horizontal rules become a divider (``────``)
    * Line breaks, emoji and bullet points are preserved.

    The result is clean plain text MAX can display without rendering glitches.
    """
    if not text:
        return text or ""

    out_lines: list[str] = []
    for raw in text.split("\n"):
        line = raw

        heading = _HEADING_RE.match(line)
        if heading:
            out_lines.append("📌 " + heading.group(2).strip())
            continue

        if _HR_RE.match(line):
            out_lines.append("────")
            continue

        line = _BOLD_RE.sub(r"\1", line)
        line = _ITAL_RE.sub(r"\1", line)
        line = _STAR_RE.sub(r"\1", line)
        line = _UND_RE.sub(r"\1", line)
        line = _CODE_RE.sub(r"\1", line)
        line = _LINK_RE.sub(r"\1 (\2)", line)
        line = _QUOTE_RE.sub("", line)
        line = _BULLET_RE.sub(r"\1• ", line)
        out_lines.append(line.rstrip())

    result = "\n".join(out_lines)
    # Collapse 3+ blank lines into a single blank line.
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


async def replace_callback_message(event: Any, text: str, *, attachments=None) -> None:
    """Acknowledge a button and replace its message in one MAX API call."""
    await event.answer(
        new_text=clean_for_max(text or " "),
        attachments=attachments if attachments is not None else [],
        notify=False,
    )


async def edit_callback_message(event: Any, text: str, *, attachments=None) -> None:
    """Edit an already-acknowledged callback message without a new bubble."""
    mid = getattr(getattr(event.message, "body", None), "mid", None)
    if not mid:
        raise ValueError("callback message has no mid")
    await event.bot.edit_message(
        str(mid),
        text=clean_for_max(text or " "),
        attachments=attachments if attachments is not None else [],
        notify=False,
    )


def chunk_text(text: str, limit: int = MAX_MESSAGE_LIMIT) -> list[str]:
    """Split ``text`` into chunks no longer than ``limit`` characters.

    Prefers splitting on newlines; falls back to a hard cut for any line longer
    than the limit. Used to stay under MAX's 4000-character message cap.
    """
    if not text:
        return []

    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), limit):
                chunks.append(line[i : i + limit])
            continue

        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


async def attach_local_image(bot: Bot, path: str | os.PathLike[str]) -> list:
    """Upload a local image and return a one-element list with the ready-to-send
    ``AttachmentUpload`` for ``attachments=``.

    ``bot.upload_media(InputMediaBuffer(...))`` returns an ``AttachmentUpload``
    whose payload is a bare ``AttachmentPayload(token=...)`` — that bare type
    fails pydantic discriminator inside ``Attachment(type=..., payload=...)``.
    The fix is to put the ``AttachmentUpload`` itself into ``attachments=`` —
    the SDK accepts it directly (it already carries ``type='image'``).

    Returns an empty list on any error so the caller can still send the
    text-only fallback.
    """
    p = Path(path)
    if not p.exists():
        logger.warning("attach_local_image: file not found: %s", p)
        return []
    try:
        buf = p.read_bytes()
        media = InputMediaBuffer(buffer=buf, filename=p.name)
        upload = await bot.upload_media(media)
        return [upload]
    except Exception as e:  # noqa: BLE001
        logger.error("attach_local_image(%s) failed: %s", p, e)
        return []


class ProgressReporter:
    """Edits ONE MAX message in-place, appending steps as they happen.

    Modeled after the Hermes TUI live progress: the user sees a single message
    that grows with each step ("🤖 Думаю…", "🔎 Ищу источники…", "✅ Готово…"),
    instead of N separate bot replies that look spammy and may get rate-limited
    by the MAX API.

    Lifecycle::

        async with ProgressReporter(event, "⏳ Обрабатываю…") as prog:
            await prog.step("🤖 Думаю…")
            await prog.step("🔎 Ищу источники…")
            ...

    * On enter: sends the initial message and stores its ``message_id``.
    * ``step(line)``: appends a line, then re-renders the message via
      ``bot.edit_message`` (rate-limited to ~1 edit per 0.8s).
    * On exit: no-op; the final LLM result is sent as a separate banner.

    Fallback: if ``edit_message`` is missing or fails, the reporter sends a
    fresh message (rate-limited) so the user still sees progress.
    """

    def __init__(
        self,
        event: Any,
        intro: str,
        *,
        min_interval: float = 0.8,
        bot: Bot | None = None,
        chat_id: int | None = None,
    ) -> None:
        self._event = event
        self._intro = intro
        self._lines: list[str] = [intro]
        self._message_id: str | None = None
        self._last_send_ts: float = 0.0
        self._min_interval = min_interval
        self._bot: Bot | None = bot
        # Опционально: явный chat_id для "прогресс-сообщения" без
        # event-контекста (используется в image_gen / hermes, где старт
        # идёт через inline-callback без message-body).
        self._chat_id: int | None = chat_id
        self._closed = False

    @property
    def message_id(self) -> str | None:
        return self._message_id

    def _resolve_bot(self) -> Bot | None:
        if self._bot is not None:
            return self._bot
        # Try common shapes: event.bot / event._bot / event.from_user.bot
        for attr in ("bot", "_bot"):
            obj = getattr(self._event, attr, None)
            if obj is not None and hasattr(obj, "edit_message"):
                self._bot = obj
                return obj
        return None

    def _extract_message_id(self, sent: Any) -> str | None:
        """Best-effort extraction of the id of a just-sent message."""
        if sent is None:
            return None
        # SendedMessage.message.body.mid
        msg = getattr(sent, "message", None)
        if msg is None:
            msg = sent
        body = getattr(msg, "body", None)
        mid = getattr(body, "mid", None) if body is not None else None
        if mid:
            return str(mid)
        for attr in ("message_id", "id"):
            val = getattr(sent, attr, None) or getattr(msg, attr, None)
            if val:
                return str(val)
        return None

    async def __aenter__(self) -> "ProgressReporter":
        # Если есть explicit bot + chat_id, шлём через sender.send
        # (например, из image_gen handler, где нет message-body в callback).
        if self._bot is not None and self._chat_id is not None:
            try:
                sender = MarkdownSender(self._bot)
                sent = await sender.send(self._chat_id, self._lines[0])
                self._message_id = self._extract_message_id(sent)
            except Exception as e:  # noqa: BLE001
                logger.warning("ProgressReporter: send failed: %s", e)
                self._message_id = None
        else:
            try:
                sent = await self._event.message.answer(self._lines[0])
                self._message_id = self._extract_message_id(sent)
            except Exception as e:  # noqa: BLE001
                logger.warning("ProgressReporter: initial answer failed: %s", e)
                self._message_id = None
        # Seed the cooldown clock so the FIRST step() respects min_interval.
        # Without this, _last_send_ts=0 makes every step pass the cooldown.
        self._last_send_ts = time.monotonic()
        return self

    async def _send_or_edit(self, body: str) -> None:
        """Try to edit the live message; fall back to sending a new one."""
        now = time.monotonic()
        if now - self._last_send_ts < self._min_interval:
            # Rate-limited: keep accumulated lines, will flush on next step.
            return
        self._last_send_ts = now

        bot = self._resolve_bot()
        # Если задан explicit chat_id (image_gen/hermes), используем его;
        # иначе берём из event.message.chat.
        chat_id = self._chat_id
        if chat_id is None:
            chat_id = getattr(
                getattr(self._event.message, "chat", None), "chat_id", None
            )

        # Preferred path: edit the same message in place.
        if bot is not None and self._message_id and hasattr(bot, "edit_message"):
            try:
                await bot.edit_message(self._message_id, text=body)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ProgressReporter: edit_message failed (%s) — falling back to send",
                    e,
                )
                # edit failed -> switch to "send new" mode for the rest
                self._message_id = None

        # Fallback: send a brand new message (rate-limited by _min_interval).
        if bot is not None and chat_id is not None and hasattr(bot, "send_message"):
            try:
                # Используем MarkdownSender для format=markdown.
                sender = MarkdownSender(bot)
                sent = await sender.send(chat_id, body)
                new_id = self._extract_message_id(sent)
                if new_id:
                    # Capture for any future edit attempts.
                    self._message_id = new_id
            except Exception as e:  # noqa: BLE001
                logger.warning("ProgressReporter: send_message fallback failed: %s", e)

    async def step(self, line: str) -> None:
        """Append a step line and push the updated message if the cooldown elapsed."""
        if not line or self._closed:
            return
        self._lines.append(line)
        body = "\n".join(self._lines)
        await self._send_or_edit(body)

    async def flush(self) -> None:
        """Force a render of all accumulated lines, ignoring the rate limit."""
        if self._closed:
            return
        body = "\n".join(self._lines)
        now = time.monotonic()
        # Bypass the cooldown by resetting the clock.
        self._last_send_ts = 0.0
        await self._send_or_edit(body)
        # Re-establish cooldown so a subsequent step() doesn't immediately fire.
        self._last_send_ts = now

    async def __aexit__(self, *exc) -> None:
        self._closed = True
        return None


async def send_home_button(
    bot: Bot,
    chat_id: int,
    *,
    reply_to_mid: str | None = None,
) -> bool:
    """Send a separate [🏠 В меню] reply so the previous result stays visible.

    Pavel's UX rule (2026-08-19, Feature 2): when a result lands, the previous
    "В меню" button must NOT edit it away. Instead we send a NEW message
    underneath, with the home button. The new message is visually attached to
    the result via MAX's `link=NewMessageLink(REPLY, mid)`.

    Args:
      * ``bot`` — the CompliantBot instance.
      * ``chat_id`` — chat to send to.
      * ``reply_to_mid`` — id of the message to anchor the reply to. If
        ``None`` the helper falls back to a plain send (no visual link).

    Returns True on success, False on any error (silently swallowed because
    the user's main result is already delivered — the home button is a UX
    nicety, not a hard dependency).

    Bug fix (2026-08-19, V4): MAX рисует пустой блок с кнопкой, если
    text == "". Поэтому шлём короткий полезный текст.
    """
    from app.max.keyboards import home_button

    # Tiny delay so MAX's UI finishes rendering the previous result before
    # we drop a second message into the same chat. 0.2s is below the
    # chunk-delay (0.5s) used for multi-part answers.
    from app.max.executors import _HOME_REPLY_PAUSE_S
    await asyncio.sleep(_HOME_REPLY_PAUSE_S)
    sender = MarkdownSender(bot)
    # V4 (2026-08-19): явный непустой текст. MAX иначе рисует пустой
    # блок с кнопкой. Текст короткий и подсказывает что выше результат.
    home_text = "⬆️ Результат выше. Вернуться в меню?"
    try:
        if reply_to_mid:
            await sender.reply(
                chat_id=chat_id,
                reply_to_mid=reply_to_mid,
                text=home_text,
                attachments=home_button(),
            )
        else:
            await sender.send(
                chat_id=chat_id,
                text=home_text,
                attachments=home_button(),
            )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("send_home_button failed: %s", e)
        return False
