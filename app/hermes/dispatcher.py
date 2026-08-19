"""HermesDispatcher — spawns and supervises HermesSession instances.

One dispatcher instance lives on the bot's app context. It:

  * tracks active sessions in-memory by user_id (only one per user at a time);
  * runs each session as an asyncio.Task;
  * sends the final result back to MAX via the MarkdownSender path;
  * appends a [🏠 В меню] reply after the result (Feature 2 stays intact).

This module is the seam between `app.max.handlers.hermes_button` (which
creates sessions on user click) and `app.max.client.Deps` (which carries
the bot, storage, settings).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from maxapi import Bot

from app.config import Settings
from app.db.storage import Storage
from app.hermes.session import (
    DEFAULT_PROGRESS_INTERVAL_S,
    HermesSession,
    SessionConfig,
)
from app.max.formatting import MarkdownSender
from app.max.keyboards import home_button
from app.max.ui import clean_for_max

logger = logging.getLogger("maxbot.hermes.dispatcher")


# Scenarios → role mapping (для передачи в Orchestrator/LLM-fallback).
SCENARIO_TO_ROLE: dict[str, str] = {
    "plan": "marketer",
    "research": "researcher",
    "custom": "chat",
}

SCENARIO_TO_INTRO: dict[str, str] = {
    "plan": "📊 Hermes строит контент-план…",
    "research": "📝 Hermes исследует тему…",
    "custom": "🤖 Hermes выполняет задачу…",
}


def _extract_mid(sent) -> str | None:
    message = getattr(sent, "message", None) or sent
    body = getattr(message, "body", None)
    mid = getattr(body, "mid", None) if body is not None else None
    if mid:
        return str(mid)
    return None


class HermesDispatcher:
    """Один на бот. Хранит активные сессии по user_id."""

    def __init__(self, bot: Bot, storage: Storage, settings: Settings) -> None:
        self._bot = bot
        self._storage = storage
        self._settings = settings
        self._sessions: dict[int, HermesSession] = {}
        self._supervisor_tasks: set[asyncio.Task] = set()
        self._progress_message_ids: dict[int, str] = {}

    def has_active(self, user_id: int) -> bool:
        sess = self._sessions.get(user_id)
        return bool(sess and sess.is_running)

    async def spawn(
        self,
        *,
        chat_id: int,
        user_id: int,
        task: str,
        scenario: str,
    ) -> HermesSession:
        """Создать и запустить сессию; если уже есть активная — возвращает её."""
        existing = self._sessions.get(user_id)
        if existing and existing.is_running:
            return existing

        role = SCENARIO_TO_ROLE.get(scenario, "chat")
        intro = SCENARIO_TO_INTRO.get(scenario, "🤖 Hermes думает…")
        config = SessionConfig(role=role, scenario=scenario, intro=intro)

        # 1) Persist a new DB row first so we have a stable session_id.
        session_id = await self._storage.create_hermes_session(
            user_id=user_id, chat_id=chat_id,
            role=role, task=task, scenario=scenario,
        )

        # 2) Progress uses one message: first update sends, later updates edit.
        sender = MarkdownSender(self._bot)
        progress_lock = asyncio.Lock()

        async def _push_progress(lines: list[str]) -> None:
            body = clean_for_max("\n".join(lines[-8:]))
            async with progress_lock:
                mid = self._progress_message_ids.get(session_id)
                try:
                    if mid:
                        await self._bot.edit_message(
                            mid,
                            text=body,
                            attachments=[],
                            notify=False,
                        )
                    else:
                        sent = await sender.send(chat_id, body)
                        new_mid = _extract_mid(sent)
                        if new_mid:
                            self._progress_message_ids[session_id] = new_mid
                except Exception as e:  # noqa: BLE001
                    logger.warning("push_progress failed: %s", e)

        sess = HermesSession(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            task=task,
            config=config,
            storage=self._storage,
            settings=self._settings,
            on_progress=_push_progress,
        )
        self._sessions[user_id] = sess

        # 3) Spawn the worker + a tracked supervisor.
        await sess.start()
        task = asyncio.create_task(
            self._supervise(sess), name=f"hermes-supervise-{session_id}"
        )
        self._supervisor_tasks.add(task)
        task.add_done_callback(self._supervisor_tasks.discard)
        return sess

    async def aclose(self) -> None:
        """Cancel sessions/supervisors during application shutdown."""
        sessions = list(self._sessions.values())
        for session in sessions:
            await session.cancel()
        tasks = list(self._supervisor_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._supervisor_tasks.clear()
        self._sessions.clear()
        self._progress_message_ids.clear()

    async def cancel(self, user_id: int) -> bool:
        sess = self._sessions.get(user_id)
        if not sess:
            return False
        await sess.cancel()
        return True

    async def _supervise(self, sess: HermesSession) -> None:
        """Wait for the session to finish, then post the result + home button.

        V4 bug fix (2026-08-19): убран дубль send_home_button. Раньше
        supervisor шлёл два сообщения: (1) результат+кнопка одним send,
        (2) пустой home_button вторым. MAX рисовал второй как пустой
        блок — Pavel'у это выглядело как глюк. Теперь только одно
        сообщение: результат + home-кнопка inline.
        """
        try:
            result = await sess.wait()
        except Exception as e:  # noqa: BLE001
            logger.exception("supervise failed for session %s: %s", sess.id, e)
            return

        body = clean_for_max(
            f"🤖 HERMES — РЕЗУЛЬТАТ ({sess.config.scenario})\n\n"
            f"{result.text}\n\n"
            f"────\n"
            f"📋 Прогресс:\n" + "\n".join(f"• {ln}" for ln in sess.progress[-6:])
        )
        sender = MarkdownSender(self._bot)
        progress_mid = self._progress_message_ids.pop(sess.id, None)
        try:
            if progress_mid:
                await self._bot.edit_message(
                    progress_mid,
                    text=body,
                    attachments=home_button(),
                    notify=False,
                )
            else:
                await sender.send(sess.chat_id, body, attachments=home_button())
        except Exception as e:  # noqa: BLE001
            logger.warning("final reply failed: %s", e)
        # Очистить локальный кэш, если это последняя сессия для user_id.
        if self._sessions.get(sess.user_id) is sess:
            self._sessions.pop(sess.user_id, None)
        logger.info(
            "hermes session %s finished status=%s chat_id=%s",
            sess.id, result.status, sess.chat_id,
        )