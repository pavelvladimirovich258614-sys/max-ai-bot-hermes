from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from maxapi import Dispatcher

from app.max.handlers import callback_handler, free_chat, hermes_button, image_gen, menu
from app.hermes.dispatcher import HermesDispatcher
from app.hermes.session import HermesSession
from app.config import Settings
from app.max.state import clear_state, set_state


class RecordingDispatcher:
    def __init__(self):
        self.callback_handlers = []
        self.message_handlers = []

    def message_callback(self, *args, **kwargs):
        def decorator(func):
            self.callback_handlers.append(func)
            return func
        return decorator

    def message_created(self, *args, **kwargs):
        def decorator(func):
            self.message_handlers.append(func)
            return func
        return decorator


def make_event(payload: str, *, chat_id: int = 10, user_id: int = 20):
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        send_callback=AsyncMock(),
        edit_message=AsyncMock(),
    )
    event = SimpleNamespace(
        callback=SimpleNamespace(payload=payload, callback_id="cb-1"),
        message=SimpleNamespace(
            body=SimpleNamespace(mid="m-1", text="old"),
            chat=SimpleNamespace(chat_id=chat_id),
        ),
        bot=bot,
        answer=AsyncMock(),
        get_ids=lambda: (chat_id, user_id),
    )
    return event


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["research", "home", "restart"])
async def test_main_menu_navigation_replaces_clicked_message_once(payload):
    dp = RecordingDispatcher()
    deps = SimpleNamespace(storage=AsyncMock(), bot=None)
    menu.register(dp, deps)
    event = make_event(payload)
    deps.bot = event.bot

    await dp.callback_handlers[0](event)

    event.answer.assert_awaited_once()
    kwargs = event.answer.await_args.kwargs
    assert kwargs.get("new_text")
    assert kwargs.get("attachments")
    event.bot.send_message.assert_not_awaited()
    event.bot.send_callback.assert_not_awaited()
    clear_state(20)


@pytest.mark.asyncio
async def test_main_menu_defers_hermes_to_specialized_handler():
    dp = RecordingDispatcher()
    event = make_event("hermes")
    deps = SimpleNamespace(storage=AsyncMock(), bot=event.bot)
    menu.register(dp, deps)

    await dp.callback_handlers[0](event)

    event.answer.assert_not_awaited()
    event.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_hermes_button_replaces_clicked_message_once():
    dp = RecordingDispatcher()
    event = make_event("hermes")
    deps = SimpleNamespace(bot=event.bot)
    hermes_button.register(dp, deps)

    await dp.callback_handlers[0](event)

    event.answer.assert_awaited_once()
    assert event.answer.await_args.kwargs.get("new_text")
    assert event.answer.await_args.kwargs.get("attachments")
    event.bot.send_message.assert_not_awaited()
    event.bot.send_callback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("research", "on_menu_callback"),
        ("post:manual", "on_menu_callback"),
        ("image", "on_image_callback"),
        ("image:own", "on_image_callback"),
        ("hermes", "on_hermes_callback"),
        ("hermes:custom", "on_hermes_callback"),
        ("post:approve:7", "on_callback"),
    ],
)
async def test_each_callback_payload_has_one_owner(payload, expected):
    dp = Dispatcher()
    event = make_event(payload)
    deps = SimpleNamespace(
        bot=event.bot,
        storage=AsyncMock(),
        auth=None,
        publisher=AsyncMock(),
    )
    menu.register(dp, deps)
    callback_handler.register(dp, deps)
    image_gen.register(dp, deps)
    hermes_button.register(dp, deps)

    matched = []
    for handler in dp.event_handlers:
        if handler.update_type.value != "message_callback":
            continue
        if await dp._check_handler_match(handler, event, None) is not None:
            matched.append(handler.func_event.__name__)

    assert matched == [expected]


@pytest.mark.asyncio
async def test_image_payload_matches_only_image_callback_handler():
    dp = Dispatcher()
    event = make_event("image")
    deps = SimpleNamespace(
        bot=event.bot,
        storage=AsyncMock(),
        auth=None,
        publisher=AsyncMock(),
    )
    menu.register(dp, deps)
    callback_handler.register(dp, deps)
    image_gen.register(dp, deps)
    hermes_button.register(dp, deps)

    matched = []
    for handler in dp.event_handlers:
        if handler.update_type.value != "message_callback":
            continue
        result = await dp._check_handler_match(handler, event, None)
        if result is not None:
            matched.append(handler.func_event.__name__)

    assert matched == ["on_image_callback"]


@pytest.mark.asyncio
async def test_image_from_post_defaults_to_wide_16_9_and_keeps_wishes():
    dp = RecordingDispatcher()
    event = make_event("image:from_post")
    deps = SimpleNamespace(bot=event.bot)
    image_gen.register(dp, deps)

    await dp.callback_handlers[0](event)

    state = image_gen._flow(20)
    assert state["action"] == "image:ask_prompt"
    assert state["mode"] == "from_post"
    assert state["aspect"] == "16:9"
    text = event.answer.await_args.kwargs["new_text"]
    assert "Пожелания:" in text
    clear_state(20)


@pytest.mark.asyncio
async def test_image_text_state_matches_only_image_text_handler():
    dp = Dispatcher()
    event = make_event("unused")
    event.message.body.text = "calm mountain lake at dawn"
    deps = SimpleNamespace(bot=event.bot, storage=AsyncMock())
    menu.register(dp, deps)
    free_chat.register(dp, deps)
    image_gen.register(dp, deps)
    hermes_button.register(dp, deps)
    set_state(20, "image:ask_prompt", {"mode": "own", "aspect": "1:1"})

    matched = []
    for handler in dp.event_handlers:
        if handler.update_type.value != "message_created":
            continue
        result = await dp._check_handler_match(handler, event, None)
        if result is not None:
            matched.append(handler.func_event.__name__)

    assert matched == ["on_image_text"]
    clear_state(20)


@pytest.mark.asyncio
async def test_image_button_replaces_clicked_message_once():
    dp = RecordingDispatcher()
    event = make_event("image")
    deps = SimpleNamespace(bot=event.bot)
    image_gen.register(dp, deps)

    await dp.callback_handlers[0](event)

    event.answer.assert_awaited_once()
    assert event.answer.await_args.kwargs.get("new_text")
    assert event.answer.await_args.kwargs.get("attachments")
    event.bot.send_message.assert_not_awaited()
    event.bot.send_callback.assert_not_awaited()
    clear_state(20)


@pytest.mark.asyncio
async def test_post_action_handler_ignores_post_submenu_payloads():
    dp = RecordingDispatcher()
    event = make_event("post:manual")
    deps = SimpleNamespace(
        bot=event.bot,
        storage=AsyncMock(),
        auth=None,
        publisher=AsyncMock(),
    )
    callback_handler.register(dp, deps)

    await dp.callback_handlers[0](event)

    event.answer.assert_not_awaited()
    event.bot.send_callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_reject_replaces_preview_without_home_message():
    dp = RecordingDispatcher()
    event = make_event("post:reject:7")
    storage = AsyncMock()
    storage.get_publication.return_value = SimpleNamespace(
        id=7, channel="123", text="draft"
    )
    deps = SimpleNamespace(
        bot=event.bot,
        storage=storage,
        auth=None,
        publisher=AsyncMock(),
    )
    callback_handler.register(dp, deps)

    await dp.callback_handlers[0](event)

    event.answer.assert_awaited_once()
    kwargs = event.answer.await_args.kwargs
    assert "отклонено" in kwargs.get("new_text", "").lower()
    assert kwargs.get("attachments")
    event.bot.send_message.assert_not_awaited()
    event.bot.send_callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_approve_acknowledges_before_slow_publish():
    dp = RecordingDispatcher()
    event = make_event("post:approve:7")
    storage = AsyncMock()
    storage.get_publication.return_value = SimpleNamespace(
        id=7, channel="123", text="draft"
    )
    entered = __import__("asyncio").Event()
    release = __import__("asyncio").Event()

    class Publisher:
        async def resolve_channel_id(self, channel):
            return 123

        async def publish(self, chat_id, text):
            entered.set()
            await release.wait()
            return "published-mid"

    deps = SimpleNamespace(
        bot=event.bot,
        storage=storage,
        auth=None,
        publisher=Publisher(),
    )
    callback_handler.register(dp, deps)

    task = __import__("asyncio").create_task(dp.callback_handlers[0](event))
    await entered.wait()
    try:
        event.answer.assert_awaited_once()
        assert "публикую" in event.answer.await_args.kwargs.get("new_text", "").lower()
    finally:
        release.set()
        await task

    event.bot.edit_message.assert_awaited_once()
    event.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_hermes_progress_sends_once_then_edits():
    sent = SimpleNamespace(message=SimpleNamespace(body=SimpleNamespace(mid="progress-1")))
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=sent),
        edit_message=AsyncMock(),
    )
    storage = AsyncMock()
    storage.create_hermes_session.return_value = 1
    settings = Settings(_env_file=None)
    dispatcher = HermesDispatcher(bot, storage, settings)

    with patch.object(HermesSession, "start", new=AsyncMock()):
        session = await dispatcher.spawn(
            chat_id=10,
            user_id=20,
            task="test",
            scenario="custom",
        )
        session.config.progress_interval_s = 0
        await session.add_progress("Шаг 1")
        await session.add_progress("Шаг 2")

    assert bot.send_message.await_count == 1
    assert bot.edit_message.await_count == 1
    await dispatcher.aclose()


@pytest.mark.asyncio
async def test_hermes_text_does_not_send_extra_started_message():
    dp = RecordingDispatcher()
    event = make_event("unused")
    event.message.body.text = "Сделай задачу"
    session = SimpleNamespace(
        id=1,
        config=SimpleNamespace(role="chat"),
    )
    dispatcher = SimpleNamespace(
        has_active=lambda user_id: False,
        spawn=AsyncMock(return_value=session),
    )
    deps = SimpleNamespace(bot=event.bot, hermes=dispatcher)
    hermes_button.register(dp, deps)
    set_state(20, "hermes:await_task:custom")

    await dp.message_handlers[0](event)

    dispatcher.spawn.assert_awaited_once()
    event.bot.send_message.assert_not_awaited()
    clear_state(20)
