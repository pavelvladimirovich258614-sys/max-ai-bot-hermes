"""Fast, in-place handlers for post approve/reject/edit callbacks."""
from __future__ import annotations

import logging

from maxapi import Dispatcher
from maxapi.filters import F
from maxapi.types import MessageCallback

from app.max.handlers.deps import Deps
from app.max.keyboards import home_button, main_menu_keyboard, post_submenu_keyboard
from app.max.ui import edit_callback_message, header, replace_callback_message

logger = logging.getLogger("maxbot.callback_handler")


def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_callback(
        F.callback.payload.startswith("post:approve:")
        | F.callback.payload.startswith("post:reject:")
        | F.callback.payload.startswith("post:edit:")
    )
    async def on_callback(event: MessageCallback) -> None:
        payload = (event.callback.payload or "").strip()
        if payload.startswith("image:"):
            return
        if not payload.startswith(("post:approve:", "post:reject:", "post:edit:")):
            return
        try:
            _, action, pub_id_str = payload.split(":", 2)
            pub_id = int(pub_id_str)
        except (ValueError, TypeError):
            await event.answer(notification="⚠️ Некорректная кнопка.")
            return

        pub = await deps.storage.get_publication(pub_id)
        if pub is None:
            await event.answer(notification="⚠️ Публикация не найдена.")
            return

        _chat_id, user_id = event.get_ids()
        if action in ("approve", "reject", "edit"):
            if deps.auth is not None and not deps.auth.is_admin(user_id):
                await event.answer(
                    notification="⚠️ Только админ может управлять публикациями."
                )
                logger.warning(
                    "post callback %s blocked: user_id=%s not admin",
                    action,
                    user_id,
                )
                return

        if action == "approve":
            # Acknowledge immediately before channel/network operations.
            await replace_callback_message(
                event,
                "⏳ ПУБЛИКУЮ ПОСТ…",
                attachments=[],
            )
            try:
                channel_id = await deps.publisher.resolve_channel_id(pub.channel)
                if channel_id is None:
                    await edit_callback_message(
                        event,
                        header("⚠️", "КАНАЛ НЕ НАЙДЕН", [
                            f"Не удалось распознать канал «{pub.channel}».",
                            "Передайте числовой chat_id канала.",
                        ]),
                        attachments=post_submenu_keyboard(),
                    )
                    return
                mid = await deps.publisher.publish(channel_id, pub.text)
                await deps.storage.update_publication(
                    pub_id,
                    status="published",
                    published_message_id=mid,
                )
                await edit_callback_message(
                    event,
                    header("✅", "ОПУБЛИКОВАНО", [
                        f"Канал: {pub.channel}",
                        "Пост успешно отправлен.",
                    ]),
                    attachments=main_menu_keyboard(),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("post approve failed: %s", exc)
                await edit_callback_message(
                    event,
                    header("⚠️", "ОШИБКА ПУБЛИКАЦИИ", [
                        "Пост не отправлен.",
                        f"Причина: {type(exc).__name__}",
                    ]),
                    attachments=post_submenu_keyboard(),
                )
            return

        if action == "reject":
            await deps.storage.update_publication(pub_id, status="rejected")
            await replace_callback_message(
                event,
                header("🚫", "ОТКЛОНЕНО", ["Черновик помечен как отклонённый."]),
                attachments=main_menu_keyboard(),
            )
            return

        if action == "edit":
            await deps.storage.update_publication(pub_id, status="edited")
            await replace_callback_message(
                event,
                header("✏️", "РЕДАКТИРОВАНИЕ", [
                    "Пришлите новый текст публикации в этот чат.",
                    "Для выхода нажмите «В меню».",
                ]),
                attachments=home_button(),
            )
            return
