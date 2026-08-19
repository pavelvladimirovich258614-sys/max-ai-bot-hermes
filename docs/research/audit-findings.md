# Audit findings

Дата: 2026-08-19

Общий объём: 52 файла, ~5086 LOC. Проверены: dead code, TODO/HACK, дублирование,
inline-магические числа, bare except, hardcoded UI strings, инкапсуляция.

## [HIGH] app/max/handlers/image_gen.py:544-609 — дублирование ProgressReporter_by_event

Полная копия `ProgressReporter` (`app/max/ui.py:167-310`) — другой интерфейс
конструктора (`(bot, chat_id, intro)` вместо `(event, intro)`), но та же роль и
та же реализация (rate-limit, edit_message, send fallback). Свой `__import__("time")`
внутри функций, magic `0.6` для min_interval, ручной разбор `getattr(msg, "body", None)`
для извлечения mid.

Фикс: добавить `bot: Bot | None = None` и `chat_id: int | None = None` в
`ProgressReporter` (ui.py:191) и заменить `_set(user_id, …)` callbacks на
`async with ProgressReporter(event, intro, bot=..., chat_id=...)`. Удалить
`ProgressReporter_by_event` (66 строк).

## [HIGH] app/max/executors.py:78 — неиспользуемая константа `_HOME_REPLY_PAUSE_S = 0.2`

Определена, но не используется. В `app/max/ui.py:341` тот же `0.2` лежит
inline: `await asyncio.sleep(0.2)`. Две точки зияют — рассинхрон в будущем.

Фикс: импортировать `_HOME_REPLY_PAUSE_S` в `ui.py` или удалить константу
и оставить magic number с комментарием.

## [HIGH] app/llm/image_client.py:243 — захардкоженный timeout `60.0`

`httpx.Timeout(60.0, connect=10.0)` для URL fetch повторяет `self._timeout` (по
умолчанию 60.0 на той же строке 80) — при изменении дефолта download не
пересчитается. Также `timeout_s=60.0` (строка 80) — magic number, не
управляемый из Settings.

Фикс: вынести `image_download_timeout_s` в `Settings` (config.py секция
"Image generation") и заменить на `self._settings.image_download_timeout_s`.

## [HIGH] app/max/handlers/image_gen.py:519-524 — обход инкапсуляции Storage

Прямой доступ к `deps.storage._conn` (private attr) и ручной `commit()` для
обновления `image_path` после INSERT. Ломает транзакционную границу: между
INSERT и UPDATE другая корутина может увидеть пустой `image_path`.

Фикс: добавить `Storage.update_generated_image_path(image_id, path)` рядом
с `update_generated_image_preview` (storage.py:225), переиспользовать
существующую транзакцию через `update_generated_image_path`.

## [HIGH] app/middleware/auth.py:22 — AuthGate инстанциируется, но не используется

`AuthGate` создаётся в `app/max/client.py:80` и кладётся в `Deps.auth`, но
`grep -rn "auth\."` по всему `app/` — ноль вызовов. Методы `is_admin` /
`require_admin` — мёртвый код. В `app/max/handlers/start.py:21` флаг is_admin
в `upsert_user` тоже не передаётся.

Фикс: либо начать гейтить админ-команды через `deps.auth.require_admin(user_id)`
в нужных handlers, либо убрать AuthGate и поле `auth: AuthGate` из
`Deps` (deps.py:21).

## [MED] app/max/keyboards.py:112-128 — `post_approval_keyboard` мёртвый

Функция определена, но `grep -rn "post_approval_keyboard"` показывает только
объявление. Реально используется `post_publish_keyboard` (executors.py:483).

Фикс: удалить `post_approval_keyboard` (17 строк) — функция устарела после
введения `post_publish_keyboard`.

## [MED] app/max/keyboards.py:150-163 — `home_button()` и `home_reply_keyboard()` идентичны

Обе возвращают `[home_markup()]`. Различие только в docstring-комментариях
("intentional naming"). 22 grep-хита — два имени для одного и того же
вызывают путаницу.

Фикс: оставить только `home_button()`, переименовать импорты `home_reply_keyboard`
на `home_button` (callback_handler.py:14, ui.py:336, dispatcher.py:30).

## [MED] app/max/handlers/image_gen.py:493-494 — `attach_to` parameter бесполезен

В `_send_preview` (строки 459-484) `attach_to` принимается, проверяется
`if mid is None and attach_to is not None:` — и блок пустой (`pass`). Эта
логика должна была привязать image к publication, но просто недописана.

Фикс: вызвать `await deps.storage.update_generated_image_attachment(image_id, attach_to)`
или удалить `attach_to` из сигнатуры `_send_preview` и убрать вычисление
`mid` на 487-491.

## [MED] app/max/handlers/image_gen.py:486-491 — извлечение `mid` бессмысленно

`mid = None` после `await sender.send(...)`, но дальше `mid` не используется
(см. выше — `attach_to` ветка делает `pass`). Бесполезное присваивание.

Фикс: удалить блок 486-491 целиком или использовать mid в `update_generated_image_preview`.

## [MED] app/max/formatting.py:111-113 — задокументированный `markdown-html-fallback` не реализован

Docstring MarkdownSender упоминает режим `"markdown-html-fallback"` и
`_markdown_to_html`, но обе функции отсутствуют. `message_format` обрабатывается
только в `executors.py:124, 251` через `"markdown"` vs `"plain"`.

Фикс: либо реализовать (например, через `_markdown_to_html` с markdown-it-py),
либо убрать обещание из docstring — пользователь / разработчик ждёт
функциональность, которая не существует.

## [MED] app/max/handlers/callback_handler.py:94 — bare `except Exception: pass`

```python
except Exception:  # noqa: BLE001
    pass
```
Нет logging. Если `_drop_home_button` упадёт — пользователь не получит
"🏠 В меню" и в логах ничего. Условие `return` с `user_chat_id = None`
выше покрывает только один кейс.

Фикс: `logger.warning("drop_home_button: %s", e)` плюс всё.

## [MED] app/db/storage.py:225-243 — мёртвые CRUD-методы

`update_generated_image_preview` и `update_generated_image_attachment`
определены, но никогда не вызываются (`grep -rn` пусто). Прямая запись
через `_conn` в image_gen.py обходит оба метода.

Фикс: либо использовать эти методы в image_gen.py (см. HIGH выше), либо
удалить.

## [MED] app/db/storage.py:81-100 — таблицы `antispam_messages` / `antispam_bans` никогда не пишутся и не читаются

CREATE TABLE есть, INSERT/UPDATE/SELECT методов нет. Колонки `raw_text`,
`text_hash`, `banned_until` — мёртвые.

Фикс: либо реализовать антиспам (было бы полезно), либо удалить таблицы
из `_create_tables`.

## [MED] app/tools/web_search.py:28 — `WebSearch` инстанциируется только в `__init__`

`grep -rn "WebSearch("` — ноль вызовов в коде, только в docstring
`analyzer.py:31`. Если LLM попросит "искать в DuckDuckGo" — пайплайн
сломается.

Фикс: либо подключить WebSearch к executor для `researcher` роли
(см. `ROLE_STEPS.researcher` в executors.py:30), либо удалить файл.

## [MED] app/middleware/rate_limit.py — модуль-пустышка

`from app.middleware.auth import AuthGate, make_rate_limiter` + `__all__`.
Никто не импортирует `from app.middleware.rate_limit`. Дублирует
`app.middleware.auth` без пользы.

Фикс: удалить `rate_limit.py` или перенести `make_rate_limiter` в
`auth.py` (он там уже есть, строка 18).

## [MED] app/max/handlers/callback_handler.py:15 — `Publisher` импортирован, не используется

`from app.max.publisher import Publisher` — но `Publisher` ни разу не
упоминается в теле функции. Остался после рефакторинга.

Фикс: удалить import.

## [MED] app/max/handlers/image_gen.py:569, 584, 590 — `__import__("time")` / `import time as _t`

В трёх местах внутри функций ProgressReporter_by_event. Хардкод названия
модуля и `import time as _t` в середине функции — стандартный import
`import time` наверху был бы чище.

Фикс: добавить `import time` на верх файла и заменить все три вызова
на `time.monotonic()`.

## [MED] app/max/ui.py:19-20 — неиспользуемые импорты `AttachmentType`, `UploadType`

Импортированы, но `grep -n` показывает ноль использований. `attach_local_image`
работает только с `InputMediaBuffer`.

Фикс: удалить обе строки импорта.

## [MED] app/llm/image_client.py:24 — неиспользуем `import os`

В файле нет ни одного `os.`. Опечатка/устарело.

Фикс: удалить.

## [MED] app/max/executors.py:90 — `for attr in ("chat_id",):` — single-element loop

Бессмысленный цикл по tuple с одним элементом. Либо список (event, attr)
реально нужен, либо `for attr in ("chat_id",)` — просто bag.

Фикс: `v = getattr(event, "chat_id", None)` без цикла, или удалить
функцию `_resolve_chat_id` если она не критична — `event.message.chat.chat_id`
достаточно.

## [MED] app/max/handlers/image_gen.py:255, 297, 360 — `chat_id` обязателен, но `int` без проверки

`_generate_raw`, `_generate_from_post`, `_regenerate` принимают `chat_id: int`,
но в `on_image_text` (строка 191) он берётся из `event.get_ids()` — может
быть `None`. При `chat_id=None` упадёт позже в `MarkdownSender.send`.

Фикс: сделать `chat_id: int | None`, после `MarkdownSender.send` ранний
return с понятным сообщением.

## [LOW] app/max/handlers/image_gen.py:592 — magic number `0.6` в min_interval

В `ProgressReporter_by_event` rate-limit `0.6` жёстко. У основного
`ProgressReporter` (ui.py:196) — `min_interval=0.8`. Расхождение без
объяснения.

Фикс: вынести в `MIN_PROGRESS_INTERVAL_S` в `app.max.ui`, использовать
в обоих классах.

## [LOW] app/max/handlers/start.py:37 — `format="markdown"` inline, обход MarkdownSender

Все остальные ответы идут через `MarkdownSender(event.bot)`, а start —
через `event.message.answer(text, format="markdown")`. Это работает
(эквивалентно), но противоречит политике "centralised MarkdownSender".

Фикс: `await MarkdownSender(event.bot).send(chat_id, text, attachments=...)`.

## [LOW] app/max/handlers/menu.py:151-180 — fallback `prompts` dict почти неиспользуем

Словарь `prompts` (research/copy/plan/analyze/ideate/prompt) — fallback для
случая, когда `COMMAND_DESCRIPTIONS` не содержит payload. По факту
`COMMAND_DESCRIPTIONS` покрывает все эти ключи (descriptions.py:16-66),
поэтому fallback мёртв.

Фикс: удалить 30 строк fallback-словаря; если `description` is None —
return с ошибкой.

## [LOW] app/llm/client.py:57-62 — `stream()` без stream-реализации

`async def stream(...)` просто вызывает `chat()` и yields. Метод
помечен "Yields text chunks" — но chunks ровно один.

Фикс: либо реализовать streaming через httpx (существующая инфраструктура
есть), либо пометить метод deprecated.

## [LOW] app/max/handlers/image_gen.py:74 — `set_state(user_id, action, **extra)` теряет `data`

`_set` делает `set_state(user_id, action, **extra)` — это распаковывает
extra как kwargs, но `set_state` (state.py:14) принимает только `data: dict`.
`**extra` молча проглатывается если сигнатура state.py не расширится.

Фикс: либо изменить `set_state` чтобы принимать `**data`, либо
передавать `data={...}` явно в `_set`.

## [LOW] app/core/orchestrator.py:44-62 — `image_prompt` отсутствует в `ROLE_SKILLS`

`ROLE_SYSTEM_MODULES` (строка 37) содержит `image_prompt`, но
`ROLE_SKILLS` (44-62) — нет. `marketer` тоже отсутствует. Inconsistency.

Фикс: добавить `"image_prompt": ["comfyui", "baoyu-infographic"]` (или
подходящие) и `"marketer": [...]`.

## [LOW] app/llm/image_client.py:107-117 — `subject_image_url` parameter без caller

`generate()` принимает `subject_image_url: str | None = None`, фича
image-to-image полностью не интегрирована в image_gen handler — там
вызывается только `image_client.generate(prompt, aspect_ratio=…)`.

Фикс: либо добавить UI "📷 Картинка-референс" в image_gen handler, либо
удалить параметр.

## [LOW] app/max/handlers/image_gen.py:438-446 — повтор `if not extracted` в `do_analyze`

`_send_final` с `answer="⚠️ Не удалось загрузить страницу. ..."` —
тот же текст, что в `do_copy` (executors.py:373-377). Дублирование
обработки.

Фикс: вынести в `_USER_MSG_FETCH_FAIL` константу в `app.max.ui`.

## [LOW] app/main.py:50 — `dp._Dispatcher__ready(bot)` (name-mangled private)

В webhook-mode идёт прямой вызов `dp._Dispatcher__ready` — это
private SDK-метод, mangled. Любое обновление maxapi ломает бутстрап.

Фикс: дождаться, пока maxapi выставит public `_init_dispatcher` или
переключиться полностью на polling для dev/test.

## [LOW] app/llm/client.py:125-127 — `_log_meta` пишет `chars=-1`

```python
logger.info("... chars=%d ...", -1, ...)
```
`signed int` вместо реальной длины. Бросает в глаза в логах.

Фикс: `sum(len(m.get("content", "")) for m in messages)` или удалить поле.

---

## Summary

На найденные категории:
- **Дублирование**: 3 HIGH (ProgressReporter, 60.0, _HOME_REPLY_PAUSE_S)
- **Мёртвый код**: 6 MED (post_approval_keyboard, home_*, attach_to, mid, empty tup loop, unused CRUD, antispam tables, WebSearch, rate_limit.py, Publisher import, __import__("time"), unused imports)
- **Bare except**: 1 MED (callback_handler.py:94)
- **Hardcoded magic numbers**: 2 MED (60.0 in image_client.py, 0.6 in image_gen.py)
- **Encapsulation bypass**: 1 HIGH (image_gen.py:519-524)
- **Hardcoded UI strings**: 0 в UI (все на русском, в описаниях английский в COMMANDS — это фолбэк на /help)
- **TODO/FIXME comments**: 0 — но несколько "Pavel" notes в комментариях

## Summary: 5 high, 16 medium, 10 low
