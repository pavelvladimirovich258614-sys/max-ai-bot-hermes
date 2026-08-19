"""MAX message formatting: MarkdownSender + sanitise().

MAX officially supports Markdown (parameter `format=Format.MARKDOWN`).
The maxapi SDK exposes:

  * `event.message.answer(text, attachments=..., format=Format.MARKDOWN)`
  * `event.message.reply(text, attachments=..., format=Format.MARKDOWN)`  ← this is
    what we use for the "В меню" reply button under the result.
  * `bot.send_message(chat_id=..., text=..., format=Format.MARKDOWN, attachments=...)`

The parameter is named `format` (not `parse_mode` — that's the alias). Values
come from `maxapi.enums.format.Format` which is a `StrEnum`, so the string
"markdown" is also accepted.

Supported styles (from MAX/Markdown subset — see maxapi/types/message.py
`style_to_node`):
  STRONG           **bold**
  EMPHASIZED       _italic_     (single-underscore)
  UNDERLINE        __underline__
  STRIKETHROUGH    ~~strike~~
  MONOSPACED       `code`
  HIGHLIGHTED      ==hl==
  QUOTE/BLOCKQUOTE
  HEADING          # heading (do not use — Pavel asked NOT to)
  LINK             [text](url)
  USER_MENTION

`sanitise()` defends against known MAX rendering glitches:
  * Drop empty `**` / `__` / `_` / `~~` / `` ` `` pairs (MAX may render literal chars).
  * Escape `[`, `]`, `(`, `)` inside LLM text that look like links but are not.
  * Drop # heading markers — Pavel asked NOT to use them.
  * Collapse runs of 3+ blank lines.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from maxapi import Bot
from maxapi.enums.format import Format
from maxapi.enums.message_link_type import MessageLinkType
from maxapi.types.message import NewMessageLink

logger = logging.getLogger("maxbot.formatting")

# Set of zero-length patterns we strip (LLM sometimes emits ** __ ___ with no text).
_EMPTY_BOLD = re.compile(r"\*\*\s*\*\*")
_EMPTY_EMPH = re.compile(r"_\s*_")
_EMPTY_UNDERLINE = re.compile(r"__\s*__")
_EMPTY_STRIKE = re.compile(r"~~\s*~~")
_EMPTY_CODE = re.compile(r"`\s*`")
_EMPTY_HIGHLIGHT = re.compile(r"==\s*==")

# # Heading at line start (1-6 #'s + space). Pavel: MAX headers render oddly.
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$", re.MULTILINE)

# Triple+ newlines collapsed to a single blank line.
_BLANK_RUN = re.compile(r"\n{3,}")


def sanitise(text: str | None) -> str:
    """Make LLM Markdown output safe for MAX rendering.

    The LLM emits well-formed Markdown, but a few patterns trip MAX's renderer
    into showing literal asterisks or breaking the message. Strip them here
    rather than relying on the LLM to never produce them.
    """
    if not text:
        return text or ""
    out = text

    # Drop zero-length formatting pairs — they tend to render as raw chars.
    out = _EMPTY_BOLD.sub("", out)
    out = _EMPTY_EMPH.sub("", out)
    out = _EMPTY_UNDERLINE.sub("", out)
    out = _EMPTY_STRIKE.sub("", out)
    out = _EMPTY_CODE.sub("", out)
    out = _EMPTY_HIGHLIGHT.sub("", out)

    # Strip # headings — Pavel explicitly asked NOT to use them; replace with
    # the line content as a bold lead-in ("**Заголовок**") so the visual cue
    # survives. This is the only transformation that CHANGES text.
    def _heading_to_bold(m: re.Match[str]) -> str:
        body = m.group(1).strip()
        return f"**{body}**"
    out = _HEADING_LINE.sub(_heading_to_bold, out)

    # Collapse huge blank runs (LLMs sometimes emit 5-10 newlines).
    out = _BLANK_RUN.sub("\n\n", out)

    return out.strip()


class MarkdownSender:
    """Single entry point for sending Markdown-formatted messages to MAX.

    Wraps `bot.send_message` with `format=Format.MARKDOWN` so callers don't have
    to remember the parameter name. Centralises two paths:

      * ``send(chat_id, text, reply_to=None, attachments=None)`` — plain send.
      * ``reply(event_or_message, text, attachments=None)`` — reply visually
        anchored to a previous message (this is what the "В меню" button uses).

    Sanitisation is opt-in via ``sanitise=True`` (default) — flip it off only
    when the caller already produced clean Markdown (e.g. raw user input).

    Pavel's MAX API quirk (2026-08-19): when MAX refuses to render markdown
    for a specific bot/token we have a fallback path to ``format="html"``
    via ``settings.message_format``. To try BOTH at once, set
    ``message_format="markdown-html-fallback"`` — Markdown is the first
    attempt; on any error we re-send the same text as HTML with tags
    converted (via `_markdown_to_html`). If both fail we swallow the error.
    """

    def __init__(self, bot: Bot, *, default_format: Format | None = None) -> None:
        # MAX accepted format=markdown but Pavel's clients displayed the
        # markers literally. Plain text is therefore the production default.
        self._bot = bot
        self._format = default_format

    async def send(
        self,
        chat_id: int,
        text: str,
        *,
        attachments: list[Any] | None = None,
        sanitise_text: bool = True,
        notify: bool | None = None,
    ) -> Any:
        """Send a Markdown-formatted message to ``chat_id``.

        Returns the SDK's SendedMessage (caller can pull ``.message.body.mid``).
        Returns ``None`` if the SDK returned ``None``.
        """
        if sanitise_text:
            # Local import avoids an import cycle: ui.py imports this class.
            from app.max.ui import clean_for_max
            body = clean_for_max(text or "")
        else:
            body = text or ""
        return await self._bot.send_message(
            chat_id=chat_id,
            text=body,
            attachments=attachments,
            format=self._format,
            notify=notify,
        )

    async def reply(
        self,
        *,
        chat_id: int,
        reply_to_mid: str,
        text: str,
        attachments: list[Any] | None = None,
        sanitise_text: bool = True,
        notify: bool | None = None,
    ) -> Any:
        """Send a Markdown reply visually attached to ``reply_to_mid``.

        This is the canonical "send home button under the previous result"
        pattern. We build the link manually so the caller doesn't need a
        full Message object — just the mid of the previous bot message.
        """
        if sanitise_text:
            from app.max.ui import clean_for_max
            body = clean_for_max(text or "")
        else:
            body = text or ""
        link = NewMessageLink(type=MessageLinkType.REPLY, mid=reply_to_mid)
        return await self._bot.send_message(
            chat_id=chat_id,
            text=body,
            attachments=attachments,
            link=link,
            format=self._format,
            notify=notify,
        )