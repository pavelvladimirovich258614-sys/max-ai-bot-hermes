"""Shared command executors used by both the legacy text commands and the new
button-driven menu. Keeping them here avoids duplicating the orchestration +
storage + "home" button wiring between the two entry points.

Keyboard building lives in `app.max.keyboards` — every reply's attachments are
pulled from there (no inline builders duplicated across handlers).

UI helpers (`header`, `clean_for_max`, `chunk_text`, `send_home_button`) live
in `app.max.ui`. Markdown handling lives in `app.max.formatting` — MAX
officially supports Markdown via `format=Format.MARKDOWN`, so by default we
send LLM output as Markdown. Setting `MESSAGE_FORMAT=plain` in .env reverts
to the old plain-text-with-emoji behaviour without code changes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import app.config as config_mod
from app.max.formatting import MarkdownSender, sanitise
from app.max.handlers.deps import Deps
from app.max.keyboards import home_button, post_publish_keyboard
from app.max.ui import ProgressReporter, chunk_text, clean_for_max, header

logger = logging.getLogger("maxbot.executors")

# Per-role scripted "thinking" steps shown to the user via ProgressReporter.
ROLE_STEPS: dict[str, tuple[str, ...]] = {
    "researcher": (
        "🔎 Ищу источники (DuckDuckGo)…",
        "📰 Собираю цитаты…",
        "📊 Структурирую бриф…",
    ),
    "copywriter": (
        "🧐 Разбираю вход (тема / черновик / ссылка)…",
        "✍️ Пишу варианты (идея → черновик → полировка)…",
        "🧹 Чищу AI-измы…",
    ),
    "analyzer": (
        "🌐 Загружаю страницу…",
        "📄 Извлекаю тезисы (trafilatura)…",
        "🤖 Разбираю через LLM…",
    ),
    "ideator": (
        "💡 Подбираю 10 разных углов…",
        "🎯 Фильтрую слабые идеи…",
    ),
    "prompt_engineer": (
        "🛠 Составляю структуру промпта…",
        "⚖️ Проверяю антипаттерны…",
    ),
    "marketer": (
        "📅 Составляю контент-план…",
        "🧩 Балансирую форматы (карусель / лонгрид / сторис)…",
    ),
}

# Banner (emoji, title) shown above each role's LLM result.
ROLE_BANNERS: dict[str, tuple[str, str]] = {
    "researcher": ("🔍", "RESEARCH"),
    "copywriter": ("✍️", "COPY"),
    "marketer": ("📅", "КОНТЕНТ-ПЛАН"),
    "ideator": ("💡", "IDEATE"),
    "prompt_engineer": ("🎯", "PROMPT"),
    "analyzer": ("🔬", "ANALYZE"),
}

# Overall orchestration budget. Provider calls use 25s each and switch to the
# fallback immediately on timeout; this budget also leaves room for web tools.
_LLM_TIMEOUT_S = 75.0
# Pause after ProgressReporter closes so the final edit lands before we send
# the banner as a separate message.
_POST_PROGRESS_PAUSE_S = 0.3
# Inter-chunk delay for multi-part replies to keep MAX API happy.
_CHUNK_DELAY_S = 0.5
# Delay before sending the [🏠 В меню] reply so MAX's UI has time to render
# the previous result message we are anchoring to.
_HOME_REPLY_PAUSE_S = 0.2


def _resolve_chat_id(event) -> int | None:
    """Best-effort chat_id from a MessageCreated / MessageCallback event."""
    msg = getattr(event, "message", None)
    if msg is not None:
        chat = getattr(msg, "chat", None)
        if chat is not None:
            cid = getattr(chat, "chat_id", None)
            if cid is not None:
                return int(cid)
    for attr in ("chat_id",):
        v = getattr(event, attr, None)
        if v is not None:
            return int(v)
    return None


def _extract_mid(sent: Any) -> str | None:
    """Best-effort extraction of the message id of a just-sent message."""
    if sent is None:
        return None
    msg = getattr(sent, "message", None) or sent
    body = getattr(msg, "body", None)
    mid = getattr(body, "mid", None) if body is not None else None
    if mid:
        return str(mid)
    for attr in ("message_id", "id"):
        v = getattr(sent, attr, None) or getattr(msg, attr, None)
        if v:
            return str(v)
    return None


async def _safe_send(event, text: str, *, attachments=None) -> str | None:
    """Send one plain-text chunk; retry without attachments on rejection."""
    body = clean_for_max(text or "")
    chat_id = _resolve_chat_id(event)
    if chat_id is not None:
        sender = MarkdownSender(event.bot)  # compatibility name; plain by default
        try:
            sent = await sender.send(chat_id, body, attachments=attachments)
            return _extract_mid(sent)
        except Exception as e:  # noqa: BLE001
            logger.warning("safe_send: central send failed: %s — fallback", e)

    try:
        sent = await event.message.answer(body, attachments=attachments)
        return _extract_mid(sent)
    except Exception as e:  # noqa: BLE001
        logger.warning("safe_send: answer(with-attachments) failed: %s", e)
        if attachments is not None:
            try:
                sent = await event.message.answer(body)
                return _extract_mid(sent)
            except Exception as e2:  # noqa: BLE001
                logger.error("safe_send: answer(no-attachments) failed: %s", e2)
                return None
        return None


async def _send_long(event, text: str, *, attachments=None) -> str | None:
    """Send ``text`` to the user, chunking to stay under MAX's 4000-char cap.

    Returns the ``mid`` of the LAST delivered chunk (used by callers to
    anchor a follow-up reply like the [🏠 В меню] button), or ``None`` if the
    first chunk failed.
    """
    chunks = chunk_text(text)
    if not chunks:
        return None
    first, *rest = chunks
    last_mid = await _safe_send(event, first, attachments=attachments)
    if last_mid is None:
        return None
    for chunk in rest:
        await asyncio.sleep(_CHUNK_DELAY_S)
        mid = await _safe_send(event, chunk)
        if mid is None:
            return last_mid
        last_mid = mid
    return last_mid


async def _play_role_steps(prog: ProgressReporter, role: str) -> None:
    """Push scripted thinking lines for a role with a small UX pause."""
    await prog.step("🤖 Думаю…")
    steps = ROLE_STEPS.get(role, ())
    for line in steps:
        await prog.step(line)
        await asyncio.sleep(0.25)


async def _safe_orchestrator_run(
    deps: Deps,
    *,
    role: str,
    task: str,
    context: dict | None,
    chat_id: int | None,
    user_id: int | None,
) -> str:
    """Run the orchestrator with timeout + full error handling.

    Returns the LLM answer, or a user-facing error string on:
      * LLM timeout (asyncio.TimeoutError after _LLM_TIMEOUT_S)
      * Orchestrator raising any exception
    Never raises.
    """
    try:
        return await asyncio.wait_for(
            deps.orchestrator.run(
                role=role,
                task=task,
                context=context,
                chat_id=chat_id,
                user_id=user_id,
            ),
            timeout=_LLM_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "orchestrator timeout role=%s after %.0fs", role, _LLM_TIMEOUT_S
        )
        return (
            f"⏱ LLM не ответил за {_LLM_TIMEOUT_S:.0f}с. "
            "Попробуй ещё раз или упрости запрос."
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("orchestrator crashed role=%s: %s", role, e)
        return (
            "⚠️ Не удалось получить ответ от LLM. "
            f"Техническая причина: {type(e).__name__}: {e}"
        )


async def _send_final(
    deps: Deps,
    event,
    *,
    role: str,
    chat_id: int | None,
    user_id: int | None,
    answer: str,
) -> None:
    """Persist assistant message, send the result, and reply with [🏠 В меню].

    Pipeline:
      1. Send the LLM answer as a NEW message (markdown or plain per
         MESSAGE_FORMAT) WITHOUT any inline button.
      2. Capture the result's ``mid``.
      3. Send a SEPARATE reply below it with only the [🏠 В меню] button,
         anchored to that ``mid`` so MAX renders it as a visual reply.

    The reply is fired via ``app.max.ui.send_home_button`` and errors are
    swallowed — the user already got the result, the home button is a UX
    nicety.
    """
    await asyncio.sleep(_POST_PROGRESS_PAUSE_S)
    if not answer or not answer.strip():
        answer = "⚠️ LLM вернул пустой ответ. Попробуй переформулировать запрос."
    await deps.storage.add_message(chat_id, user_id, "assistant", answer)
    fmt = config_mod.get_settings().message_format.lower()
    if fmt == "markdown":
        # V4 bug fix (2026-08-19): Pavel подтвердил — MAX UI **не рендерит**
        # markdown для нашего бота (`**жирный**` приходит как литералы).
        # Пробуем отправить с format=markdown, но если MAX не рендерит,
        # LLM-вывод с `**` идёт юзеру как мусор. Поэтому прогоняем через
        # clean_for_max() чтобы `**жирный**` → `жирный` (plain) плюс
        # структура через эмодзи ЗАГЛАВКИ.
        # Реальный fallback: `MESSAGE_FORMAT=plain` в .env отключает format.
        emoji_title = ROLE_BANNERS.get(role)
        plain_answer = clean_for_max(answer)
        if emoji_title is not None:
            emoji, title = emoji_title
            banner = header(emoji, f"{title} — РЕЗУЛЬТАТ")
            body = f"{banner}\n\n{plain_answer}"
        else:
            body = plain_answer
    else:
        emoji_title = ROLE_BANNERS.get(role)
        if emoji_title is not None:
            emoji, title = emoji_title
            body = header(emoji, f"{title} — РЕЗУЛЬТАТ", [clean_for_max(answer)])
        else:
            body = clean_for_max(answer)
    # Send the result WITHOUT the home button — Feature 2 rule.
    last_mid = await _send_long(event, body)
    if last_mid is None:
        return
    # Fire the dedicated [🏠 В меню] reply. Best-effort — we don't want a
    # UX-only failure to bubble up.
    from app.max.ui import send_home_button
    target_chat = _resolve_chat_id(event) or chat_id
    if target_chat is None:
        return
    await send_home_button(
        event.bot, target_chat, reply_to_mid=last_mid,  # type: ignore[arg-type]
    )


async def run_role(
    deps: Deps,
    event,  # MessageCreated
    role: str,
    task: str,
    intro: str = "⏳ Обрабатываю…",
) -> None:
    """Run an orchestrator role and reply with the answer + [🏠 В меню]."""
    chat_id, user_id = event.get_ids()
    async with ProgressReporter(event, intro) as prog:
        await _play_role_steps(prog, role)
        answer = await _safe_orchestrator_run(
            deps,
            role=role,
            task=task,
            context={"source": "max", "entry": "menu"},
            chat_id=chat_id,
            user_id=user_id,
        )
        await prog.step("✅ Готово — формирую ответ…")
        await prog.flush()
    await _send_final(
        deps, event,
        role=role, chat_id=chat_id, user_id=user_id, answer=answer,
    )


# ---- per-role entry points (called after the user supplies text) ----

async def do_research(deps: Deps, event, text: str) -> None:
    """F2: parse ``<topic> [freshness]`` and run the three-tier cascade.

    Backwards-compat: if the user did not append a freshness window
    (e.g. legacy ``/research AI in legal``), we default to 30d and run
    the cascade exactly like the F2.5 handler does. If the cascade
    returns a FAILED result, we still send the JSON to the user so
    they see the diagnostic — the /status command can confirm the
    full chain.
    """
    from app.core.research_cascade import ResearchCascade
    from app.schemas.research import parse_freshness
    from app.max.formatting import MarkdownSender
    from app.max.keyboards import home_button

    # Parse ``<topic> [freshness]`` — last token is the window if it
    # matches a known suffix. Otherwise, topic = whole text, window =
    # default "30d".
    parts = (text or "").split()
    freshness_token = parts[-1] if parts and parts[-1].lower() in (
        "7d", "30d", "90d", "all",
    ) else None
    if freshness_token:
        topic = " ".join(parts[:-1]).strip()
        freshness_window = parse_freshness(freshness_token)
    else:
        topic = (text or "").strip()
        freshness_window = "30d"

    if not topic:
        await _safe_send(
            event,
            "Использование: /research <тема> [7d|30d|90d|all]",
            attachments=home_button(),
        )
        return

    chat_id, user_id = event.get_ids()
    intro = f"🔎 Исследую «{topic}» (окно: {freshness_window})…"
    sender = MarkdownSender(event.bot)
    progress_mid: str | None = None
    try:
        progress_msg = await sender.send(chat_id, intro)
        progress_mid = (
            getattr(getattr(progress_msg, "message", None), "body", None) and
            getattr(progress_msg.message.body, "mid", None)
        )
    except Exception:  # noqa: BLE001
        progress_mid = None

    settings = app_config.get_settings()
    cascade = ResearchCascade(settings)
    try:
        result = await cascade.run(topic, freshness_window)
    finally:
        await cascade.aclose()

    # Render: status banner + compact JSON.
    status_emoji = {
        "OK": "✅",
        "PARTIAL": "🟡",
        "NO_FRESH_DATA": "⚠️",
        "FAILED": "❌",
    }.get(result.status, "🔍")
    body = (
        f"{status_emoji} RESEARCH — {result.status}\n"
        f"Окно: {result.freshness_window}  "
        f"Tier 1: {result.tier_summary.tier1_urls}  "
        f"Tier 2: {result.tier_summary.tier2_crawled}  "
        f"Tier 3: {result.tier_summary.tier3_verified}  "
        f"Hermes: {result.tier_summary.hermes_enrichment}\n\n"
        f"📝 {result.answer}\n\n"
        f"```json\n{result.to_compact_json()}\n```"
    )
    body = clean_for_max(body)

    # Edit the progress message in place if we have a mid; otherwise
    # send a new one.
    if progress_mid is not None:
        try:
            await event.bot.edit_message(
                progress_mid, text=body, attachments=home_button(), notify=False,
            )
        except Exception:  # noqa: BLE001
            await _safe_send(event, body, attachments=home_button())
    else:
        await _safe_send(event, body, attachments=home_button())


def _classify_copy_input(text: str) -> tuple[str, str]:
    """Detect the copywriter input mode and produce a routed prompt."""
    s = text.strip()
    if not s:
        return "TOPIC", text
    lower = s.lower()
    if lower.startswith("http://") or lower.startswith("https://"):
        return "URL", f"ВХОД: ССЫЛКА {s}"
    if len(s) > 800 or s.count("\n") >= 6:
        return "DOCUMENT", f"ВХОД: СЫРОЙ ДОКУМЕНТ\n\n{s}"
    if " | " in s and len(s) < 200:
        return "TOPIC", f"ВХОД: ТЕМА\n\n{s}"
    return "DRAFT", f"ВХОД: СЫРОЙ ЧЕРНОВИК\n\n{s}"


async def _fetch_url(settings: config_mod.Settings, url: str, max_chars: int = 6000) -> str:
    """Fetch a URL and return extracted text; empty string on failure."""
    from app.tools.web_reader import WebReader

    reader = WebReader(settings)
    try:
        text = await reader.read(url, max_chars=max_chars)
        return text or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_url(%s) failed: %s", url, e)
        return ""
    finally:
        try:
            await reader.aclose()
        except Exception:  # noqa: BLE001
            pass


async def do_copy(deps: Deps, event, text: str) -> None:
    """Copywriter entry point — detects mode, fetches URL if needed, runs LLM."""
    chat_id, user_id = event.get_ids()
    mode, routed = _classify_copy_input(text)
    intro = {
        "URL": "🌐 Скачиваю ссылку и пишу пост…",
        "DOCUMENT": "📄 Разбираю документ и собираю пост…",
        "DRAFT": "✍️ Превращаю черновик в готовый пост…",
        "TOPIC": "✍️ Пишу пост на тему…",
    }.get(mode, "✍️ Пишу пост…")

    async with ProgressReporter(event, intro) as prog:
        await prog.step("🧐 Определяю режим входа…")
        await prog.step(f"Режим: {mode}")
        if mode == "URL":
            await prog.step("🌐 Загружаю страницу (trafilatura)…")
            settings = config_mod.get_settings()
            url = text.strip()
            extracted = await _fetch_url(settings, url)
            if not extracted:
                await prog.step("⚠️ Не удалось загрузить страницу")
                await prog.flush()
                await _send_final(
                    deps, event,
                    role="copywriter", chat_id=chat_id, user_id=user_id,
                    answer=(
                        "⚠️ Не удалось загрузить страницу по ссылке. "
                        "Проверь URL и доступность сайта."
                    ),
                )
                return
            await prog.step("🤖 Готовлю пост из тезисов страницы…")
            task = (
                f"ВХОД: ССЫЛКА {url}\n\n"
                f"Содержание страницы (извлечённый текст):\n\n{extracted}"
            )
        else:
            task = routed
            await prog.step("🤖 Пишу варианты…")
        answer = await _safe_orchestrator_run(
            deps,
            role="copywriter",
            task=task,
            context={
                "source": "max", "entry": "menu", "copy_mode": mode,
            },
            chat_id=chat_id, user_id=user_id,
        )
        await prog.step("✅ Готово — формирую ответ…")
        await prog.flush()
    await _send_final(
        deps, event,
        role="copywriter", chat_id=chat_id, user_id=user_id, answer=answer,
    )


async def do_plan(deps: Deps, event, text: str) -> None:
    await run_role(
        deps, event, "marketer",
        f"Составь контент-план: {text}. Если N указан — ровно N строк.",
        "📅 Составляю контент-план…",
    )


async def do_ideate(deps: Deps, event, text: str) -> None:
    await run_role(deps, event, "ideator", text, "💡 Генерирую идеи…")


async def do_prompt(deps: Deps, event, text: str) -> None:
    await run_role(deps, event, "prompt_engineer", text, "🛠 Помогаю с промптом…")


async def do_analyze(deps: Deps, event, url: str) -> None:
    chat_id, user_id = event.get_ids()
    if not (url.startswith("http://") or url.startswith("https://")):
        await _safe_send(
            event,
            "⚠️ Пришлите корректный URL (http/https).",
            attachments=home_button(),
        )
        return
    async with ProgressReporter(event, "🌐 Скачиваю и разбираю страницу…") as prog:
        await prog.step("🌐 Загружаю URL…")
        await prog.step("📄 Извлекаю текст (trafilatura)…")
        settings = config_mod.get_settings()
        extracted = await _fetch_url(settings, url)
        if not extracted:
            await prog.step("⚠️ Не удалось загрузить страницу")
            await prog.flush()
            await asyncio.sleep(_POST_PROGRESS_PAUSE_S)
            await _send_final(
                deps, event,
                role="analyzer", chat_id=chat_id, user_id=user_id,
                answer=(
                    "⚠️ Не удалось загрузить страницу. "
                    "Проверь URL и доступность сайта."
                ),
            )
            return
        await prog.step("🤖 Разбираю через LLM…")
        answer = await _safe_orchestrator_run(
            deps,
            role="analyzer",
            task=f"URL: {url}\n\nТекст страницы:\n{extracted}",
            context={"source": "max", "entry": "menu", "url": url},
            chat_id=chat_id, user_id=user_id,
        )
        await prog.step("✅ Готово — формирую ответ…")
        await prog.flush()
    await _send_final(
        deps, event,
        role="analyzer", chat_id=chat_id, user_id=user_id, answer=answer,
    )


async def do_post(deps: Deps, event, text: str) -> None:
    """Create a post draft with approve/edit/reject inline buttons."""
    chat_id, user_id = event.get_ids()
    s = config_mod.get_settings()
    if s.hermes_mode == "none" and not s.llm_api_key:
        # No LLM polish available; still allow draft + manual publish.
        pass
    if not text or " " not in text:
        await _safe_send(
            event,
            "Использование: <channel_id> <текст>\n"
            "Например: 123456 Новый пост про ИИ",
            attachments=home_button(),
        )
        return
    channel, _, post_text = text.partition(" ")
    pub_id = await deps.storage.create_publication(
        chat_id=chat_id or 0, channel=channel, text=post_text
    )
    preview = f"📝 Превью поста в канал {channel}:\n\n{post_text}"
    await _safe_send(event, preview, attachments=post_publish_keyboard(pub_id))