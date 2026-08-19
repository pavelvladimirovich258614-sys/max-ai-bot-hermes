"""HermesSession — one Hermes task run from MAX (Feature V3, 2026-08-19).

Spawn вариант A (минимальный): subprocess через `hermes peer dm rza`,
с прогресс-апдейтами в MAX каждые 30 секунд и таймаутом 5 минут.

Why subprocess: текущий HermesClient уже использует `hermes peer dm rza`
через CLI subprocess. Pavel не зарегистрировал peer RZA для бота, поэтому
spawn гарантированно провалится. Но это даёт шанс — если Pavel настроит
peer, кнопка сразу начнёт работать.

Fallback: in-process LLM через Orchestrator.run() — работает всегда.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from app.config import Settings
from app.core.orchestrator import Orchestrator
from app.db.storage import Storage

logger = logging.getLogger("maxbot.hermes.session")

# Hermes session tunables (вынесены в константы — можно вытащить в config.py позже)
DEFAULT_TIMEOUT_S: float = 300.0    # 5 минут
DEFAULT_PROGRESS_INTERVAL_S: float = 30.0  # апдейт в MAX каждые 30с
DEFAULT_PROGRESS_LINES_MAX: int = 8  # храним последние N строк прогресса


@dataclass
class SessionConfig:
    """Что варьируется между сценариями."""

    role: str = "chat"
    scenario: str = "custom"
    intro: str = "🤖 Hermes думает…"
    timeout_s: float = DEFAULT_TIMEOUT_S
    progress_interval_s: float = DEFAULT_PROGRESS_INTERVAL_S


@dataclass
class SessionResult:
    """Финальный результат сессии."""

    status: str  # 'done' | 'failed' | 'timeout'
    text: str = ""
    progress: list[str] = field(default_factory=list)


ProgressCallback = Callable[[list[str]], Awaitable[None]]


def build_cli_argv(command: str, task: str) -> list[str]:
    """Split the configured executable safely and append task as one argument."""
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        parts = [
            part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part
            for part in parts
        ]
    if not parts:
        raise ValueError("hermes_rza_cli is empty")
    return [*parts, task]


def clean_hermes_cli_output(text: str) -> str:
    """Remove Hermes CLI startup diagnostics from text intended for MAX users."""
    lines = [
        line for line in text.splitlines()
        if not line.startswith("Warning: Unknown toolsets:")
    ]
    return "\n".join(lines).strip()


class HermesSession:
    """Одна Hermes-сессия (одна запущенная задача от MAX-пользователя)."""

    def __init__(
        self,
        *,
        session_id: int,
        chat_id: int,
        user_id: int,
        task: str,
        config: SessionConfig,
        storage: Storage,
        settings: Settings,
        on_progress: ProgressCallback,
    ) -> None:
        self.id = session_id
        self.chat_id = chat_id
        self.user_id = user_id
        self.task = task
        self.config = config
        self._storage = storage
        self._settings = settings
        self._on_progress = on_progress
        self._progress: list[str] = [config.intro]
        self._task_handle: Optional[asyncio.Task] = None
        self._process: Optional[asyncio.subprocess.Process] = None
        self._communicate_task: Optional[asyncio.Task] = None
        self._done = asyncio.Event()
        self._result: Optional[SessionResult] = None
        self._started_ts = time.monotonic()
        self._last_progress_ts = self._started_ts

    @property
    def progress(self) -> list[str]:
        return list(self._progress)

    @property
    def is_running(self) -> bool:
        return not self._done.is_set()

    @property
    def result(self) -> Optional[SessionResult]:
        return self._result

    async def add_progress(self, line: str) -> None:
        """Append a progress line, persist it, and notify the callback."""
        if not line:
            return
        self._progress.append(line)
        # Keep the buffer bounded.
        if len(self._progress) > DEFAULT_PROGRESS_LINES_MAX:
            self._progress = self._progress[-DEFAULT_PROGRESS_LINES_MAX:]
        try:
            await self._storage.update_hermes_session_progress(
                self.id, list(self._progress),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("session %s: persist progress failed: %s", self.id, e)
        now = time.monotonic()
        first_update = len(self._progress) == 2
        due = now - self._last_progress_ts >= self.config.progress_interval_s
        if first_update or due:
            try:
                await self._on_progress(list(self._progress))
            except Exception as e:  # noqa: BLE001
                logger.warning("session %s: progress callback failed: %s", self.id, e)
            self._last_progress_ts = now

    async def start(self) -> None:
        """Spawn the work as an asyncio task. Caller awaits ``wait()``."""
        self._task_handle = asyncio.create_task(
            self._run(), name=f"hermes-session-{self.id}",
        )

    async def wait(self) -> SessionResult:
        """Block until the session finishes or times out."""
        if self._task_handle is None:
            await self.start()
        try:
            await asyncio.wait_for(self._done.wait(), timeout=self.config.timeout_s)
        except asyncio.TimeoutError:
            await self._stop_worker()
            await self._finish_with_timeout()
        return self._result  # type: ignore[return-value]

    async def _stop_worker(self) -> None:
        """Stop process and worker task without leaving Windows transports."""
        proc = self._process
        if proc is not None and proc.returncode is None:
            proc.kill()
        comm = self._communicate_task
        if comm is not None and not comm.done():
            try:
                await asyncio.wait_for(asyncio.shield(comm), timeout=2.0)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                comm.cancel()
                await asyncio.gather(comm, return_exceptions=True)
        task = self._task_handle
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def cancel(self) -> None:
        """Cancel a running session and close its subprocess."""
        await self._stop_worker()
        if not self._done.is_set():
            await self._finish("failed", "Отменено пользователем")

    # ----------------- internals -----------------

    async def _run(self) -> None:
        """Try Hermes CLI → fall back to in-process LLM Orchestrator."""
        await self.add_progress(f"📋 Роль: {self.config.role}")
        await self.add_progress(f"🎬 Сценарий: {self.config.scenario}")
        await self.add_progress("🔌 Пробую Hermes CLI…")
        text = await self._try_hermes_cli()
        if text:
            await self._finish("done", text)
            return
        await self.add_progress("⚠️ Hermes CLI недоступен → использую LLM напрямую")
        text = await self._try_llm_fallback()
        if text:
            await self._finish("done", text)
            return
        await self._finish("failed", "Не удалось получить ответ ни от Hermes, ни от LLM.")

    async def _try_hermes_cli(self) -> Optional[str]:
        """Run Hermes CLI without a shell and report progress periodically."""
        argv = build_cli_argv(self._settings.hermes_rza_cli, self.task)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._process = proc
            communicate = asyncio.create_task(proc.communicate())
            self._communicate_task = communicate
            deadline = time.monotonic() + min(60.0, self.config.timeout_s)
            interval = max(0.1, self.config.progress_interval_s)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    out, err = await communicate
                    logger.warning("Hermes CLI timed out err=%s", err[:200])
                    return None
                done, _ = await asyncio.wait(
                    {communicate}, timeout=min(interval, remaining)
                )
                if communicate in done:
                    out, err = communicate.result()
                    if proc.returncode == 0:
                        body = out.decode("utf-8", errors="replace").strip()
                        return body or None
                    logger.warning("Hermes CLI rc=%s err=%s", proc.returncode, err[:200])
                    if b"No peer named" in err and b"rza" in err.lower():
                        return await self._try_direct_hermes_chat()
                    return None
                elapsed = time.monotonic() - self._started_ts
                await self.add_progress(f"⏳ Hermes работает… ({elapsed:.0f}с)")
        except FileNotFoundError:
            await self.add_progress("⚠️ `hermes` CLI не найден в PATH")
            return None
        except asyncio.CancelledError:
            proc = self._process
            if proc is not None and proc.returncode is None:
                proc.kill()
            comm = self._communicate_task
            if comm is not None:
                await asyncio.gather(comm, return_exceptions=True)
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Hermes CLI spawn failed: %s", e)
            return None
        finally:
            self._process = None
            self._communicate_task = None

    async def _try_direct_hermes_chat(self) -> Optional[str]:
        """Run the local Hermes agent when the configured RZA peer is absent."""
        argv = ["hermes", "chat", "-Q", "--source", "max-bot", "-q", self.task]
        await self.add_progress("🤖 Peer RZA не настроен — запускаю Hermes Agent напрямую…")
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._process = proc
            communicate = asyncio.create_task(proc.communicate())
            self._communicate_task = communicate
            deadline = time.monotonic() + min(60.0, self.config.timeout_s)
            interval = max(0.1, self.config.progress_interval_s)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    _out, err = await communicate
                    logger.warning("direct Hermes CLI timed out err=%s", err[:200])
                    return None
                done, _ = await asyncio.wait(
                    {communicate}, timeout=min(interval, remaining)
                )
                if communicate in done:
                    out, err = communicate.result()
                    if proc.returncode == 0:
                        body = clean_hermes_cli_output(
                            out.decode("utf-8", errors="replace")
                        )
                        return body or None
                    logger.warning("direct Hermes CLI rc=%s err=%s", proc.returncode, err[:200])
                    return None
                elapsed = time.monotonic() - self._started_ts
                await self.add_progress(f"⏳ Hermes Agent работает… ({elapsed:.0f}с)")
        except FileNotFoundError:
            await self.add_progress("⚠️ `hermes` CLI не найден в PATH")
            return None
        except asyncio.CancelledError:
            proc = self._process
            if proc is not None and proc.returncode is None:
                proc.kill()
            comm = self._communicate_task
            if comm is not None:
                await asyncio.gather(comm, return_exceptions=True)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("direct Hermes CLI spawn failed: %s", exc)
            return None
        finally:
            self._process = None
            self._communicate_task = None

    async def _try_llm_fallback(self) -> Optional[str]:
        """In-process fallback: Orchestrator.run() через MiniMax primary LLM.

        Audit fix (2026-08-19, V3 phase 2): Orchestrator's signature is
        ``Orchestrator(settings, llm, storage)`` — not ``(settings, storage)``.
        We instantiate the LLMClient here (it's stateless except for an
        httpx client, and aclose() is called in the finally below).
        """
        from app.llm.client import LLMClient
        llm = LLMClient(self._settings)
        try:
            orch = Orchestrator(self._settings, llm=llm, storage=self._storage)
            try:
                ctx = {
                    "source": "max",
                    "entry": "hermes_button",
                    "scenario": self.config.scenario,
                }
                await self.add_progress("🧠 LLM думает…")
                return await asyncio.wait_for(
                    orch.run(
                        role=self.config.role,
                        task=self.task,
                        context=ctx,
                        chat_id=self.chat_id,
                        user_id=self.user_id,
                    ),
                    timeout=self.config.timeout_s,
                )
            finally:
                try:
                    await orch.aclose()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await llm.aclose()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            logger.exception("LLM fallback failed: %s", e)
            return None

    async def _finish(self, status: str, text: str) -> None:
        if self._done.is_set():
            return
        self._result = SessionResult(status=status, text=text, progress=list(self._progress))
        try:
            await self._storage.finish_hermes_session(
                self.id, status=status, result_text=text,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("session %s: persist finish failed: %s", self.id, e)
        self._done.set()

    async def _finish_with_timeout(self) -> None:
        await self._finish(
            "timeout",
            f"⏱ Hermes думал дольше {self.config.timeout_s:.0f}с. "
            "Прерываю — попробуй короче или разбей на части.",
        )