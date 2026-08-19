"""Image generation handler (Feature 3, 2026-08-19).

Flow
----
User lands here from one of three places:

  1. `image` callback (from post submenu).
  2. `image:attach:<pub_id>` callback (from a post draft's [🎨 Добавить картинку]).
  3. (Could be expanded: free-text command `/image …`).

The handler is stateful — each user can have at most one in-flight image-gen
flow at a time. We store the flow's progress in `state` so the matching
text-input handler knows what to do with the next message.

Stages
------
  STEP_ASK_SOURCE     — "Свой промпт или из поста?" → own / from_post
  STEP_ASK_ASPECT     — aspect ratio → 1:1 / 16:9 / 9:16 / 3:4 / 4:3
  STEP_ASK_PROMPT     — free-text prompt input
  STEP_GENERATING     — ProgressReporter + ImageClient (no callback here)
  STEP_PREVIEW        — preview with [📤 В канал] [🔄 Перегенерировать] [🏠 В меню]

Storage of generated images lives in `app.db.storage` (`generated_images` table).
The PNG bytes go to ``settings.image_storage_dir/<id>.png`` — we download
immediately because the URL expires in 24h.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from maxapi import Dispatcher
from maxapi.filters import F
from maxapi.types import MessageCallback, MessageCreated

from app.config import Settings, get_settings
from app.llm.client import LLMClient
from app.llm.image_client import ImageClient, ImageGenError, user_message_for
from app.max.executors import _safe_orchestrator_run
from app.max.formatting import MarkdownSender
from app.max.handlers.deps import Deps
from app.max.handlers.state_filters import StateActionFilter
from app.max.keyboards import (
    callback_message,
    home_button,
    image_aspect_keyboard,
    image_preview_keyboard,
    post_with_image_keyboard,
)
from app.max.state import clear_state, get_state, set_state
from app.max.ui import (
    ProgressReporter,
    attach_local_image,
    replace_callback_message,
    send_home_button,
)

logger = logging.getLogger("maxbot.image_gen")

# FSM states (mirrored in handlers/menu.py via the same `set_state` mechanism).
_STEP_ASK_SOURCE = "image:ask_source"      # → image:own or image:from_post
_STEP_ASK_ASPECT = "image:ask_aspect"      # → image:aspect:<r>
_STEP_ASK_PROMPT = "image:ask_prompt"      # next free text = prompt
_STEP_PREVIEW = "image:preview"            # preview + [📤 / 🔄 / ✏️ / 🏠]


def _flow(user_id: int) -> dict | None:
    """Return the current image-flow state for the user, or None."""
    s = get_state(user_id)
    if s and s.get("action", "").startswith("image:"):
        return s
    return None


def _clear(user_id: int) -> None:
    clear_state(user_id)


def _set(user_id: int, action: str, **extra) -> None:
    set_state(user_id, action, extra)


def register(dp: Dispatcher, deps: Deps) -> None:
    """Hook up callback + text-input handlers."""

    @dp.message_callback(
        (F.callback.payload == "image") | F.callback.payload.startswith("image:")
    )
    async def on_image_callback(event: MessageCallback) -> None:
        payload = (event.callback.payload or "").strip()
        chat_id, user_id = event.get_ids()

        if payload == "image":
            _set(user_id, _STEP_ASK_SOURCE)
            await replace_callback_message(
                event,
                "🎨 ГЕНЕРАЦИЯ КАРТИНКИ (IMAGE-01)\n\n"
                "Откуда взять промпт?\n"
                "• Свой — напишешь промпт сам\n"
                "• Из поста — LLM превратит черновик в визуальный промпт",
                attachments=post_with_image_keyboard(),
            )
            return

        if payload == "image:own":
            _set(user_id, _STEP_ASK_ASPECT, mode="own")
            await replace_callback_message(
                event,
                "1️⃣ ВЫБЕРИ ПРОПОРЦИИ КАРТИНКИ",
                attachments=image_aspect_keyboard(),
            )
            return

        if payload == "image:from_post":
            _set(user_id, _STEP_ASK_PROMPT, mode="from_post", aspect="16:9")
            await replace_callback_message(
                event,
                "📝 ПРИШЛИ ТЕКСТ ПОСТА\n\n"
                "Сделаю широкий профессиональный visual для image-01, 16:9 по умолчанию: "
                "сцена, которая передаёт суть поста и пробивает баннерную слепоту.\n\n"
                "В конце добавь свои требования строкой «Пожелания: …» — "
                "например: «Пожелания: тёплый свет, без людей, дорогой editorial стиль».\n"
                "Для другой пропорции укажи «Формат: 9:16» или «Формат: 1:1».",
                attachments=home_button(),
            )
            return

        if payload.startswith("image:aspect:"):
            aspect = payload[len("image:aspect:"):]
            if aspect not in _VALID_ASPECTS:
                await _menu_ack(event, f"⚠️ Неподдерживаемая пропорция {aspect}.")
                return
            flow = _flow(user_id) or {}
            mode = flow.get("mode", "own")
            if mode == "own":
                _set(user_id, _STEP_ASK_PROMPT, mode="own", aspect=aspect)
                await replace_callback_message(
                    event,
                    "✍️ НАПИШИ ПРОМПТ ДЛЯ КАРТИНКИ\n\n"
                    "До 1500 символов. Короткие визуальные описания "
                    "на английском image-01 понимает лучше.",
                    attachments=home_button(),
                )
            else:  # from_post → we already collected the post text
                post_text = flow.get("post_text", "")
                await _menu_ack(event, "")
                await _generate_from_post(
                    deps, event.bot, chat_id, user_id,
                    post_text=post_text, aspect=aspect,
                )
            return

        if payload.startswith("image:regen:"):
            try:
                image_id = int(payload[len("image:regen:"):])
            except ValueError:
                return
            await _menu_ack(event, "🔄 Перегенерирую…")
            await _regenerate(deps, event.bot, chat_id, user_id, image_id)
            return

        if payload.startswith("image:publish:"):
            try:
                image_id = int(payload[len("image:publish:"):])
            except ValueError:
                return
            await _menu_ack(event, "")
            await _publish_to_channel(deps, event.bot, chat_id, user_id, image_id)
            return

        if payload.startswith("image:attach:"):
            try:
                pub_id = int(payload[len("image:attach:"):])
            except ValueError:
                return
            _set(user_id, _STEP_ASK_SOURCE, attach_to=pub_id)
            await replace_callback_message(
                event,
                "🎨 КАРТИНКА ДЛЯ ЭТОГО ПОСТА\n\n"
                "Откуда взять промпт?\n"
                "• Свой — напишешь сам\n"
                "• Из поста — LLM сделает из черновика",
                attachments=post_with_image_keyboard(),
            )
            return

    # ---- text input while a flow is active ----

    @dp.message_created(StateActionFilter(exact={_STEP_ASK_PROMPT}))
    async def on_image_text(event: MessageCreated) -> None:
        text = (event.message.body.text or "").strip()
        if not text or text.startswith("/"):
            return
        chat_id, user_id = event.get_ids()
        flow = _flow(user_id)
        if flow is None:
            return  # not in image flow — let menu/free_chat handle it
        action = flow["action"]
        if action == _STEP_ASK_PROMPT:
            mode = flow.get("mode", "own")
            aspect = flow.get("aspect") or get_settings().image_aspect_default
            if mode == "own":
                # Text is the user's prompt verbatim.
                await _generate_raw(
                    deps, event.bot, chat_id, user_id,
                    prompt=text, aspect=aspect,
                    post_text="",
                    attach_to=flow.get("attach_to"),
                )
                return
            if mode == "from_post":
                # A final `Формат: 9:16` line overrides the 16:9 editorial default.
                aspect = _aspect_from_post_text(text, aspect)
                # Text is the post; LLM will turn it into an image prompt.
                await _generate_from_post(
                    deps, event.bot, chat_id, user_id,
                    post_text=text, aspect=aspect,
                    attach_to=flow.get("attach_to"),
                )
                return


# ---------------------- helpers ----------------------

_VALID_ASPECTS = {"1:1", "4:3", "3:4", "16:9", "9:16", "2:3", "3:2", "21:9"}
_ASPECT_LINE_RE = re.compile(
    r"(?im)^\s*(?:формат|соотношение|aspect|format)\s*:\s*(\d+:\d+)\s*$"
)


def _aspect_from_post_text(post_text: str, fallback: str) -> str:
    """Allow an explicit ``Формат: 9:16`` line to override the wide default."""
    match = _ASPECT_LINE_RE.search(post_text)
    requested = match.group(1) if match else None
    return requested if requested in _VALID_ASPECTS else fallback


async def _generate_bytes(settings: Settings, prompt: str, aspect: str) -> bytes:
    """Run one image request and always close its HTTP connection pool."""
    async with ImageClient(settings) as client:
        return await client.generate(prompt, aspect_ratio=aspect)


async def _menu_ack(event: MessageCallback, text: str | None = None) -> None:
    """Acknowledge image-menu click without creating a chat bubble."""
    # V5 (2026-08-19): `MessageForCallback("")` is rendered by MAX as a
    # blank white message. Use an ephemeral notification instead.
    await event.answer(notification=text or "Готово")


async def _send_text(bot, chat_id: int, text: str, *, attachments=None) -> None:
    """Send text via MarkdownSender with `format=markdown`."""
    sender = MarkdownSender(bot)  # type: ignore[arg-type]
    await sender.send(chat_id, text, attachments=attachments)


# ---------------------- generate paths ----------------------

async def _generate_raw(
    deps: Deps,
    bot,
    chat_id: int,
    user_id: int,
    *,
    prompt: str,
    aspect: str,
    post_text: str,
    attach_to: int | None,
) -> None:
    """User-supplied prompt → image-01 → preview."""
    s = get_settings()

    intro = "🎨 Генерирую картинку…"
    async with ProgressReporter(None, intro, bot=bot, chat_id=chat_id) as prog:
        await prog.step(f"Пропорции: {aspect}")
        await prog.step("Запрос к image-01…")
        try:
            png = await _generate_bytes(s, prompt, aspect)
        except ImageGenError as e:
            msg = user_message_for(e)
            await prog.step(msg)
            await prog.flush()
            await _send_text(bot, chat_id, msg, attachments=home_button())
            _clear(user_id)
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("image: unexpected error: %s", e)
            await prog.step(f"⚠️ Неожиданная ошибка: {e}")
            await prog.flush()
            await _send_text(bot, chat_id, "⚠️ Не удалось сгенерировать картинку.", attachments=home_button())
            _clear(user_id)
            return
        await prog.step("✅ Готово — сохраняю…")
        await prog.flush()

    image_id, image_path = await _save_image(
        deps, user_id, post_text=post_text, prompt=prompt,
        aspect=aspect, png=png,
    )
    await _send_preview(bot, chat_id, image_id, image_path, prompt, aspect, attach_to=attach_to)
    _clear(user_id)


async def _generate_from_post(
    deps: Deps,
    bot,
    chat_id: int,
    user_id: int,
    *,
    post_text: str,
    aspect: str,
    attach_to: int | None = None,
) -> None:
    """User-supplied post → image_prompt role → image-01 → preview."""
    intro = "🎨 Превращаю пост в промпт…"
    async with ProgressReporter(None, intro, bot=bot, chat_id=chat_id) as prog:
        await prog.step("Прогоняю через image_prompt роль…")
        prompt = await _safe_orchestrator_run(
            deps,
            role="image_prompt",
            task=post_text,
            context={"source": "max", "entry": "image", "aspect": aspect},
            chat_id=chat_id,
            user_id=user_id,
        )
        prompt = (prompt or "").strip()
        if not prompt:
            await prog.step("⚠️ LLM не вернул промпт")
            await prog.flush()
            await _send_text(bot, chat_id, "⚠️ LLM не смог сделать промпт из поста. Попробуй «Свой промпт».", attachments=home_button())
            _clear(user_id)
            return
        if len(prompt) > get_settings().image_prompt_max_chars:
            prompt = prompt[: get_settings().image_prompt_max_chars]
        await prog.step(f"Промпт: {prompt[:80]}{'…' if len(prompt) > 80 else ''}")
        await prog.step("Запрос к image-01…")

        s = get_settings()
        try:
            png = await _generate_bytes(s, prompt, aspect)
        except ImageGenError as e:
            msg = user_message_for(e)
            await prog.step(msg)
            await prog.flush()
            await _send_text(bot, chat_id, msg, attachments=home_button())
            _clear(user_id)
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("image: unexpected error: %s", e)
            await prog.step(f"⚠️ Неожиданная ошибка: {e}")
            await prog.flush()
            await _send_text(bot, chat_id, "⚠️ Не удалось сгенерировать картинку.", attachments=home_button())
            _clear(user_id)
            return
        await prog.step("✅ Готово — сохраняю…")
        await prog.flush()

    image_id, image_path = await _save_image(
        deps, user_id, post_text=post_text, prompt=prompt,
        aspect=aspect, png=png,
    )
    await _send_preview(bot, chat_id, image_id, image_path, prompt, aspect, attach_to=attach_to)
    _clear(user_id)


# ---------------------- regen / publish ----------------------

async def _regenerate(
    deps: Deps, bot, chat_id: int, user_id: int, image_id: int
) -> None:
    row = await deps.storage.get_generated_image(image_id)
    if row is None or row.user_id != user_id:
        await _send_text(bot, chat_id, "⚠️ Картинка не найдена.", attachments=home_button())
        return
    s = get_settings()
    intro = "🔄 Перегенерирую…"
    async with ProgressReporter(None, intro, bot=bot, chat_id=chat_id) as prog:
        await prog.step(f"Пропорции: {row.aspect_ratio}")
        try:
            png = await _generate_bytes(s, row.prompt, row.aspect_ratio)
        except ImageGenError as e:
            msg = user_message_for(e)
            await prog.step(msg)
            await prog.flush()
            await _send_text(bot, chat_id, msg, attachments=home_button())
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("image regen: %s", e)
            await prog.step(f"⚠️ Неожиданная ошибка: {e}")
            await prog.flush()
            await _send_text(bot, chat_id, "⚠️ Не удалось перегенерировать.", attachments=home_button())
            return
        await prog.step("✅ Готово — сохраняю…")
        await prog.flush()
    # Overwrite the previous file (same id) so we don't leak.
    image_path = _image_path_for(image_id, s)
    Path(image_path).write_bytes(png)
    await _send_preview(
        bot, chat_id, image_id, image_path, row.prompt, row.aspect_ratio,
    )


async def _publish_to_channel(
    deps: Deps, bot, chat_id: int, user_id: int, image_id: int
) -> None:
    """Send the image to the channel that the latest publication belongs to.

    If the image was created from "Из поста" / "Свой промпт" without a
    specific publication binding, we fall back to asking the user for a
    chat_id via the standard `/post <chat_id> <text>` flow.
    """
    row = await deps.storage.get_generated_image(image_id)
    if row is None or row.user_id != user_id:
        await _send_text(bot, chat_id, "⚠️ Картинка не найдена.", attachments=home_button())
        return
    # Find the publication this image is bound to (if any).
    pub = None
    if row.attached_to_publication_id:
        pub = await deps.storage.get_publication(row.attached_to_publication_id)
    if pub is None:
        await _send_text(
            bot, chat_id,
            "📤 **Чтобы отправить картинку в канал, привяжи её к посту.**\n\n"
            "1. Вернись в `📤 Пост в канал` → создай черновик поста.\n"
            "2. Нажми **[🎨 Добавить картинку]** под превью поста.\n"
            "3. Выбери «🤖 Из поста» (промпт возьмётся из твоего черновика).\n"
            "4. Затем нажми **[📤 В канал]** на превью картинки.",
            attachments=home_button(),
        )
        return

    image_path = row.image_path
    if not image_path or not Path(image_path).exists():
        await _send_text(bot, chat_id, "⚠️ Файл картинки не найден на диске.", attachments=home_button())
        return

    channel_id = await deps.publisher.resolve_channel_id(pub.channel)
    if channel_id is None:
        await _send_text(
            bot, chat_id,
            f"⚠️ Не удалось распознать канал «{pub.channel}». "
            f"Передайте числовой chat_id канала.",
            attachments=home_button(),
        )
        return

    await _send_text(bot, chat_id, "📤 Отправляю в канал…")
    mid = await deps.publisher.publish_with_image(channel_id, pub.text, image_path)
    if mid is None:
        await _send_text(bot, chat_id, "⚠️ Не удалось отправить пост с картинкой.", attachments=home_button())
        return
    await deps.storage.update_publication(
        pub.id, status="published", published_message_id=mid,
    )
    await _send_text(
        bot, chat_id,
        f"✅ **Опубликовано с картинкой!**\n\n"
        f"Канал: `{pub.channel}`\n"
        f"Текст поста: {pub.text}",
        attachments=home_button(),
    )
    await send_home_button(bot, chat_id)


# ---------------------- preview + storage ----------------------

async def _send_preview(
    bot,
    chat_id: int,
    image_id: int,
    image_path: str,
    prompt: str,
    aspect: str,
    *,
    attach_to: int | None = None,
) -> None:
    """Send the generated image with the action keyboard below it."""
    sender = MarkdownSender(bot)  # type: ignore[arg-type]
    attachments = await attach_local_image(bot, image_path)
    if not attachments:
        await _send_text(
            bot, chat_id,
            "⚠️ Картинка сгенерирована, но загрузить её в MAX не удалось. "
            "Файл сохранён локально.",
            attachments=image_preview_keyboard(image_id),
        )
        return
    caption = (
        f"🎨 **Картинка готова** (image-01, {aspect})\n\n"
        f"**Промпт:** _{prompt[:200]}{'…' if len(prompt) > 200 else ''}_\n\n"
        f"Что дальше?\n"
        f"• **[📤 В канал]** — отправить пост + эту картинку в канал.\n"
        f"• **[🔄 Перегенерировать]** — та же композиция заново.\n"
        f"• **[✏️ Свой промпт заново]** — новый промпт с нуля.\n"
    )
    await sender.send(
            chat_id,
            caption,
            attachments=attachments + image_preview_keyboard(image_id),
        )


async def _save_image(
    deps: Deps,
    user_id: int,
    *,
    post_text: str,
    prompt: str,
    aspect: str,
    png: bytes,
) -> tuple[int, str]:
    """Persist bytes to disk + insert DB row. Returns (id, image_path)."""
    s = get_settings()
    image_id = await deps.storage.create_generated_image(
        user_id=user_id,
        post_text=post_text,
        prompt=prompt,
        aspect_ratio=aspect,
        image_path="",  # fill below
    )
    image_path = _image_path_for(image_id, s)
    Path(image_path).parent.mkdir(parents=True, exist_ok=True)
    Path(image_path).write_bytes(png)
    # Audit HIGH #4 (2026-08-19): use public API instead of poking _conn.
    await deps.storage.update_generated_image_path(image_id, image_path)
    return image_id, image_path


def _image_path_for(image_id: int, s: Settings) -> str:
    base = s.image_storage_dir or "./data/images"
    if not os.path.isabs(base):
        base = os.path.abspath(base)
    return os.path.join(base, f"{image_id}.png")



