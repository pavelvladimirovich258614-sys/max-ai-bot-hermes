"""Publisher: sends approved posts (optionally with a cover image) to a MAX channel.

Uses the bot's send_message with chat_id (channel id). Per MAX rules:
  * Token is in the Authorization header (handled by CompliantBot).
  * Channel posts need notify=True (channels don't deliver without a push).
  * Rate limit: ≤2 messages/sec per chat — the shared semaphore covers it.
  * Attachments: `bot.upload_media(InputMediaBuffer(...))` returns an
    ``AttachmentUpload`` that we pass directly in ``attachments=`` (NOT
    wrapped in a bare ``Attachment`` — see app/max/ui.py:attach_local_image).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from maxapi import Bot
from maxapi.types.input_media import InputMediaBuffer

from app.db.storage import Storage
from app.max.ui import attach_local_image

logger = logging.getLogger("maxbot.publisher")


class Publisher:
    def __init__(self, bot: Bot, storage: Storage) -> None:
        self._bot = bot
        self._storage = storage

    async def resolve_channel_id(self, channel: str) -> Optional[int]:
        """Resolve a channel reference to a numeric chat_id."""
        channel = (channel or "").strip()
        if channel.lstrip("-").isdigit():
            return int(channel)
        return None

    async def publish(self, chat_id: int, text: str) -> Optional[str]:
        """Publish plain text to a channel."""
        msg = await self._bot.send_message(chat_id=chat_id, text=text, notify=True)
        mid = getattr(getattr(msg, "body", None), "mid", None)
        logger.info("Published to channel %s -> mid=%s", chat_id, mid)
        return mid

    async def publish_with_image(
        self,
        chat_id: int,
        text: str,
        image_path: str | os.PathLike[str],
    ) -> Optional[str]:
        """Publish ``text`` with an image attachment to a channel.

        The image is uploaded on the fly via ``attach_local_image``. On any
        upload failure we fall back to text-only publish so the post still
        gets out.
        """
        attachments = await attach_local_image(self._bot, image_path)
        if not attachments:
            logger.warning(
                "publish_with_image: image upload failed (%s) — falling back to text",
                image_path,
            )
            return await self.publish(chat_id, text)
        try:
            msg = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                attachments=attachments,
                notify=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "publish_with_image: send with attachments failed (%s) — text-only fallback",
                e,
            )
            return await self.publish(chat_id, text)
        mid = getattr(getattr(msg, "body", None), "mid", None)
        logger.info("Published to channel %s with image -> mid=%s", chat_id, mid)
        return mid