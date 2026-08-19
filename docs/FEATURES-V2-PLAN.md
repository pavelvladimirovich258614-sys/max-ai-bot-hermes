# FEATURES-V2-PLAN — Markdown / Reply-кнопка / Картинки

**Дата старта:** 2026-08-19
**Бот:** `@id752703975446_3_bot`
**Проект:** `D:\hermes-multi-agent-setup\max-ai-bot`
**Контекст:** `docs/HANDOFF.md` уже прочитан. Бот оживлён, 36/36 тестов зелёные.

---

## 🎯 Цель

Сделать 3 крупные фичи параллельно-последовательно, без отката:

1. **Markdown** — все 6 ролей и free-chat выдают **жирный/курсив/ссылки**, MAX это рендерит.
2. **Reply-кнопка "В меню"** — результат остаётся, кнопка прилетает отдельным сообщением снизу, а не edit'ом.
3. **Генерация картинок** — `/post → 🎨 Картинка → превью + [📤 В канал] [🔄 Перегенерировать] [🏠 В меню]`.

---

## 📐 Архитектура изменений

### Фича 1: Markdown (`formatting.py`)

```
app/max/formatting.py     [NEW] MarkdownSender.send() — обёртка над bot.send_message
                            с параметром format='markdown'
app/max/ui.py             [TRIM] clean_for_max() — сократить (только raw-случаи),
                            header() оставить для UI-каркаса
app/max/executors.py      [EDIT] _send_long() — прокидывает message_format
                            вместо прямого clean_for_max()
app/config.py             [EDIT] +message_format: str = "markdown"
app/llm/prompts/*.py      [EDIT] все 7 — вывод в Markdown, без # заголовков
```

### Фича 2: Reply-кнопка "В меню"

```
app/max/ui.py             [NEW] send_home_button(chat_id, reply_to_message_id)
                            Шлёт reply с одной кнопкой [🏠 В меню]
app/max/executors.py      [EDIT] _send_final() — после отправки результата
                            вызывает send_home_button(result_msg_id) вместо edit
app/max/handlers/menu.py  [EDIT] payload='home' — очищает state, шлёт
                            главное меню НОВЫМ сообщением (не edit!)
app/max/handlers/callback_handler.py [EDIT] post:approve/reject/edit — после
                            callback_message() ещё send_home_button(reply_to)
app/max/keyboards.py      [NEW] home_reply_keyboard() — одиночная кнопка
```

### Фича 3: Картинки

```
app/llm/image_client.py   [NEW] ImageClient.generate(prompt, aspect, ref)
                            httpx POST https://api.minimax.io/v1/image_generation
                            retry 2x, скачивает bytes сразу
app/llm/prompts/image_prompt.py [NEW] роль «пост → промпт для image-01»
app/max/handlers/image_gen.py  [NEW] FSM: choose_prompt → choose_aspect
                                  → preview → publish / regenerate / own_prompt_again
app/max/handlers/menu.py  [EDIT] payload='image' → ask_prompt_or_post
                            payload='image:own' / 'image:from_post'
                            payload='image:aspect:<ratio>'
                            payload='image:regen' / 'image:to_channel'
app/max/publisher.py      [EDIT] publish(text, image_bytes=None)
                            если image — upload + attachments=[image_upload]
app/max/storage.py / models.py [NEW] GeneratedImage(id, user_id, post_text,
                          prompt, aspect, image_path, created_at)
app/config.py             [EDIT] +image_aspect_default, +image_storage_dir
app/max/keyboards.py      [EDIT] post_submenu — кнопка [🎨 Сгенерировать картинку]
                          post_approval — [🎨 Добавить картинку]
```

---

## 🚦 Порядок работ

| Шаг | Что | Кто |
|---|---|---|
| 1 | Research maxapi: параметр format, reply на message_id | GZA (subagent R1) |
| 1' | Research MiniMax image_generation API | GZA (subagent R2) |
| 2 | MarkdownSender + trim clean_for_max + config + промпты | Masta Killa |
| 3 | py_compile + pytest (36+N) + restart + проверить /start | RZA |
| 4 | send_home_button + правка executors/menu/callback_handler | Masta Killa |
| 5 | py_compile + pytest + restart + клик [🏠 В меню] | RZA |
| 6 | ImageClient + storage + handlers + keyboards + publisher | Masta Killa |
| 7 | image_prompt.py роль (Cappadonna в Masta Killa) | Masta Killa |
| 8 | py_compile + pytest (39) + restart + полный e2e | RZA |
| 9 | Отчёт Pavel'у | RZA |

---

## 🚫 Чёткие ограничения

- НЕ трогать: `.env`, `bot_wrapper.py` (домен v2), `config.py` имена полей,
  `keyboards.py main_menu_keyboard()` (если не добавляем картинку),
  `start.py` (там баннер).
- НЕ добавлять: LangChain / LiteLLM / Redis / PostgreSQL / FSM-фреймворки.
- НЕ выдумывать токены. Использовать `LLM_PRIMARY_API_KEY` из `.env`.
- НЕ edit'ить старое сообщение для [🏠 В меню] — только reply.
- НЕ хранить URL от MiniMax дольше сессии — скачивать сразу.
- НЕ использовать `n>1` в image_generation.
- НЕ генерировать картинки длиннее 1500 символов промпта.

---

## ✅ Acceptance

1. `py_compile` всех файлов — чисто.
2. `pytest` — 39 passed (36 базовых + 3 новых: MarkdownSender, sanitise,
   ImageClient.generate happy-path).
3. После рестарта бота: /start → баннер+9 кнопок.
4. Любая команда → Markdown-ответ (жирный/курсив/ссылки) **видны в MAX**.
5. После команды отдельным reply прилетает [🏠 В меню], результат остаётся.
6. /post → [🎨 Сгенерировать картинку] → выбор → aspect → preview
   с 4 кнопками → [📤 В канал] публикует post+image.
7. Логи чистые, нет 400 от MAX API.
8. Финальный отчёт Pavel'у.

---

## 📝 Заметки

- MAX принимает Markdown **официально** — Pavel дал доки. Конкретное имя
  параметра (`format`/`parse_mode`) — определяет research R1.
- Image-01 модель MiniMax — текст-в-картинку и опционально
  `subject_reference` (для image-to-image).
- В MAX API attachments — список `[upload, keyboard, ...]`, где `upload` —
  результат `bot.upload_file()` (см. `app/max/ui.py:attach_local_image`).
- Bot Wrapper `CompliantBot` уже всё нужное умеет — не трогаем.