"""Smoke tests for handlers and the LLM fallback path.

These tests do NOT require network, real tokens, or the MAX SDK at import time
beyond importing pure modules. We test:
  * config loads with safe defaults (no secrets)
  * LLMClient builds and the fallback error path returns a friendly message
    without network (by feeding it a provider with no API key)
  * storage CRUD round-trips on a temp sqlite file
  * keyboards build a 3-button approval markup
"""
import os
import tempfile

import pytest

from app.config import Settings
from app.db.storage import Storage
from app.llm.client import LLMClient
from app.max.keyboards import post_publish_keyboard
from app.llm.prompts import researcher


def test_settings_defaults_no_secrets():
    s = Settings(_env_file=None)
    assert s.max_api_base == "https://platform-api2.max.ru"
    assert s.max_bot_token == ""
    assert s.llm_provider == "minimax"


def test_researcher_prompt_present():
    # F2 (2026-08-21): the researcher role is now a strict-JSON
    # Research Engineer-of-Record. The "5-7 findings" prose requirement
    # was replaced by a strict schema. We assert the new contract.
    sp = researcher.SYSTEM_PROMPT
    # The role no longer tells the LLM to "give 5-7 findings" — it
    # tells the LLM to emit a single JSON object matching ResearchResult.
    assert "ResearchResult" in sp
    # And the freshness rule is explicit.
    assert "FRESHNESS" in sp or "freshness" in sp
    # The old "5-7" finding count is no longer the contract; if it
    # ever sneaks back in we want to know, not have it silently pass.
    assert "5–7" not in sp and "5-7" not in sp


def test_start_text_is_plain_without_markdown_markers():
    from app.max.handlers import start

    build = getattr(start, "build_start_text", None)
    assert build is not None
    text = build()
    for marker in ("**", "```", "[ссылками](", "`"):
        assert marker not in text


def test_keyboards_post_publish():
    """post_publish_keyboard: [Опубликовать][Редактировать][Отклонить] + [🏠 В меню]."""
    kb_list = post_publish_keyboard(1)
    assert isinstance(kb_list, list) and len(kb_list) == 1
    kb = kb_list[0]
    # InlineKeyboardBuilder.as_markup returns an Attachment with payload.buttons
    buttons = kb.payload.buttons
    flat = [b for row in buttons for b in row]
    # post_publish_keyboard: 3 action buttons + 1 [🏠 В меню] on separate row.
    assert len(flat) == 4
    payloads = {b.payload for b in flat}
    assert {
        "post:approve:1",
        "post:edit:1",
        "post:reject:1",
        "home",
    } == payloads


def test_menu_keyboard_has_all_unique_actions():
    from app.max.keyboards import main_menu_keyboard
    kb_list = main_menu_keyboard()
    assert isinstance(kb_list, list) and len(kb_list) == 1
    kb = kb_list[0]
    flat = [b for row in kb.payload.buttons for b in row]
    # 6 actions + Post + Hermes + Help + Restart = 10 buttons (V3, 2026-08-19).
    assert len(flat) == 10, f"expected 10 buttons, got {len(flat)}"
    payloads = {b.payload for b in flat}
    assert {"research", "copy", "plan", "ideate", "analyze", "prompt",
            "post", "hermes", "help", "restart"}.issubset(payloads)
    # image-кнопки больше нет (Pavel убрал) — её перенесли в /post подменю
    assert "image" not in payloads


def test_callback_routes_to_correct_state():
    from app.max.state import get_state, set_state, clear_state
    # Simulate the menu handler setting a state on a callback.
    user_id = 555
    clear_state(user_id)
    set_state(user_id, "research")
    st = get_state(user_id)
    assert st["action"] == "research"
    clear_state(user_id)
    assert get_state(user_id) is None


def test_post_command_returns_to_menu():
    from app.max.keyboards import main_menu_keyboard, post_submenu_keyboard
    sub_list = post_submenu_keyboard()
    assert isinstance(sub_list, list) and len(sub_list) == 1
    sub = sub_list[0]
    flat = [b for row in sub.payload.buttons for b in row]
    payloads = {b.payload for b in flat}
    assert "home" in payloads  # post submenu offers a return to main menu
    # and the main menu itself is reachable from the same builder
    main_list = main_menu_keyboard()
    main = main_list[0]
    main_payloads = {b.payload for row in main.payload.buttons for b in row}
    assert "post" in main_payloads


def test_llm_fallback_no_keys_friendly():
    # Orchestrator is where the friendly fallback lives (Hermes down -> LLM down
    # -> friendly message). Disable Hermes (no network) to exercise it directly.
    import asyncio

    from app.core.orchestrator import Orchestrator

    s = Settings(_env_file=None)
    s.llm_api_key = ""
    s.llm_fallback_api_key = ""
    s.hermes_mode = "none"

    async def run():
        orch = Orchestrator(s, LLMClient(s), Storage(":memory:"))
        out = await orch.run(role="chat", task="hi")
        assert "Hermes" in out or "LLM" in out
        await orch.aclose()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_storage_roundtrip(tmp_path):
    db = os.path.join(str(tmp_path), "test.db")
    st = Storage(db)
    await st.init()
    await st.upsert_user(123, username="pavel", is_admin=True)
    u = await st.get_user(123)
    assert u is not None and u.username == "pavel" and u.is_admin is True

    await st.add_message(chat_id=10, user_id=123, role="user", text="hello")
    await st.add_message(chat_id=10, user_id=123, role="assistant", text="hi")
    ctx = await st.get_session_context(chat_id=10, user_id=123, limit=10)
    assert ctx == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]

    pid = await st.create_publication(chat_id=10, channel="999", text="post")
    await st.update_publication(pid, status="published", published_message_id="m1")
    pub = await st.get_publication(pid)
    assert pub.status == "published"
    await st.close()
