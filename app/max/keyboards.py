"""Inline keyboards for the MAX bot.

Main menu (7 buttons incl. help), post submenu, post-approval row, and the
ubiquitous [🏠 В меню] button.

All builders return a *list* (the shape `attachments=` expects in maxapi), e.g.
`attachments=main_menu_keyboard()`. For `send_callback`/`event.edit` the same
list is passed as `MessageForCallback(attachments=...)`.

`callback_message()` builds the `MessageForCallback` that `send_callback` /
`event.edit` REQUIRE — passing `message=None` makes the MAX API reject the
request with 400 'message or notification required'.
"""
from __future__ import annotations

from maxapi.types import CallbackButton
from maxapi.types.updates.message_callback import MessageForCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

# Payload that returns the user to the main menu from any sub-state.
HOME_PAYLOAD = "home"


def main_menu_keyboard() -> list:
    """10 кнопок: 6 действий + Пост + Hermes + Помощь + Перезапуск.

    Returns a list (the shape `attachments=` expects in maxapi).
    """
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="🔍 Исследовать", payload="research"),
          CallbackButton(text="✍️ Копирайтинг", payload="copy"))
    b.row(CallbackButton(text="📅 Контент-план", payload="plan"),
          CallbackButton(text="💡 Идеи", payload="ideate"))
    b.row(CallbackButton(text="🔬 Анализ ссылки", payload="analyze"),
          CallbackButton(text="🎯 Промпт", payload="prompt"))
    b.row(CallbackButton(text="📤 Пост в канал", payload="post"))
    # [🤖 Hermes] (Feature V3, 2026-08-19) — кнопка для прямого запуска Hermes.
    b.row(CallbackButton(text="🤖 Hermes", payload="hermes"))
    b.row(CallbackButton(text="❓ Помощь", payload="help"))
    b.row(CallbackButton(text="🔄 Перезапуск бота", payload="restart"))
    return [b.as_markup()]


def hermes_submenu_keyboard() -> list:
    """Подменю [🤖 Hermes]: 3 сценария + [🏠 В меню]."""
    b = InlineKeyboardBuilder()
    b.row(
        CallbackButton(text="📊 Контент-план", payload="hermes:plan"),
        CallbackButton(text="📝 Исследование", payload="hermes:research"),
    )
    b.row(CallbackButton(text="🎯 Своя задача", payload="hermes:custom"))
    b.row(CallbackButton(text="🏠 В меню", payload=HOME_PAYLOAD))
    return [b.as_markup()]


def post_submenu_keyboard() -> list:
    """Post action -> choose source of the channel id. Returns a list.

    Feature 3 (2026-08-19): the [🎨 Сгенерировать картинку] button lives
    between "Мои каналы" and the manual-id entry — it's the natural next
    step the user thinks about once they've decided WHERE to post.
    """
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="📋 Мои каналы", payload="post:my_channels"))
    b.row(CallbackButton(text="🎨 Сгенерировать картинку", payload="image"))
    b.row(CallbackButton(text="🔢 Ввести chat_id вручную", payload="post:manual"))
    b.row(CallbackButton(text="🏠 В меню", payload=HOME_PAYLOAD))
    return [b.as_markup()]


def post_with_image_keyboard() -> list:
    """[✍️ Свой промпт] [🤖 Из поста] [🏠 В меню] — first step of image gen."""
    b = InlineKeyboardBuilder()
    b.row(
        CallbackButton(text="✍️ Свой промпт", payload="image:own"),
        CallbackButton(text="🤖 Из поста", payload="image:from_post"),
    )
    b.row(CallbackButton(text="🏠 В меню", payload=HOME_PAYLOAD))
    return [b.as_markup()]


def image_aspect_keyboard() -> list:
    """[1:1] [16:9] [9:16] [3:4] [4:3] — choose aspect for image-01."""
    b = InlineKeyboardBuilder()
    b.row(
        CallbackButton(text="1:1", payload="image:aspect:1:1"),
        CallbackButton(text="16:9", payload="image:aspect:16:9"),
        CallbackButton(text="9:16", payload="image:aspect:9:16"),
    )
    b.row(
        CallbackButton(text="3:4", payload="image:aspect:3:4"),
        CallbackButton(text="4:3", payload="image:aspect:4:3"),
    )
    b.row(CallbackButton(text="🏠 В меню", payload=HOME_PAYLOAD))
    return [b.as_markup()]


def image_preview_keyboard(image_id: int) -> list:
    """[📤 В канал] [🔄 Перегенерировать] [✏️ Свой промпт] [🏠 В меню]."""
    b = InlineKeyboardBuilder()
    b.row(
        CallbackButton(text="📤 В канал", payload=f"image:publish:{image_id}"),
        CallbackButton(text="🔄 Перегенерировать", payload=f"image:regen:{image_id}"),
    )
    b.row(
        CallbackButton(text="✏️ Свой промпт заново", payload="image:own"),
        CallbackButton(text="🏠 В меню", payload=HOME_PAYLOAD),
    )
    return [b.as_markup()]


def post_publish_keyboard(publication_id: int) -> list:
    """[Опубликовать][Редактировать][Отклонить] + [🏠 В меню]. Returns a list."""
    b = InlineKeyboardBuilder()
    b.row(
        CallbackButton(text="Опубликовать", payload=f"post:approve:{publication_id}"),
        CallbackButton(text="Редактировать", payload=f"post:edit:{publication_id}"),
        CallbackButton(text="Отклонить", payload=f"post:reject:{publication_id}"),
    )
    b.row(CallbackButton(text="🏠 В меню", payload=HOME_PAYLOAD))
    return [b.as_markup()]


def home_markup():
    """Single [🏠 В меню] inline keyboard markup. Wrap in a list for attachments."""
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="🏠 В меню", payload=HOME_PAYLOAD))
    return b.as_markup()


def home_button() -> list:
    """A single [🏠 В меню] button. List shape.

    Used both as a reply button and as an inline button in `_send_long()`
    output — the markup is identical; only the sending pattern differs
    (send_message vs send_callback/edit). Single source of truth.
    """
    return [home_markup()]


def callback_message(text: str | None = None, attachments=None) -> MessageForCallback:
    """Build a `MessageForCallback` for `send_callback()` / `event.edit()`.

    `attachments` must be a list of inline-keyboard markups (or None to default
    to a single [🏠 В меню] button). Never pass `message=None` to `send_callback`
    — the MAX API rejects it with 400 'message or notification required'.
    """
    if attachments is None:
        attachments = [home_markup()]
    return MessageForCallback(text=text, attachments=attachments)
