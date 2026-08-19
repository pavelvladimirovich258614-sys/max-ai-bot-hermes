# REFACTOR-V3-PLAN — актуальный аудит и план доведения

Дата: 2026-08-19  
Проект: `D:\hermes-multi-agent-setup\max-ai-bot`  
Метод: последовательная эстафета ролей RZA → Masta Killa → Inspectah Deck; без параллельной симуляции агентов.

## 1. Состояние (✅)

Базовая проверка перед изменениями:

- `python -m compileall -q app tests` — чисто.
- `pytest -q` — 90 passed, 2 предупреждения о незакрытом Windows subprocess transport.
- 55 Python-файлов, около 5907 строк в `app/`.
- Локальная папка не содержит `.git`, поэтому история и diff недоступны.

Рабочие части:

- FastAPI lifespan, polling/webhook dispatch: `app/main.py:32-89`.
- CompliantBot с `platform-api2.max.ru` и Authorization header: `app/max/bot_wrapper.py` (защищённый файл, не изменять).
- 11 команд через актуальный `Bot.set_commands()`: `app/max/client.py:39-52,106-125`. Установленный SDK подтверждает, что метод вызывает `PATCH /me/commands`.
- 10 кнопок главного меню и Hermes-подменю: `app/max/keyboards.py:24-53`.
- FSM для обычных ролей и `post:awaiting`: `app/max/handlers/menu.py:119-153,245-274`.
- Image flow, сохранение файлов и публикация с картинкой: `app/max/handlers/image_gen.py`, `app/max/publisher.py:45-79`.
- HermesSession/Dispatcher/3 сценария: `app/hermes/session.py`, `app/hermes/dispatcher.py`, `app/max/handlers/hermes_button.py`.
- SQLite CRUD для публикаций, изображений и Hermes sessions: `app/db/storage.py:42-365`.
- Plain-text sanitation существует: `app/max/ui.py:clean_for_max`, `app/llm/skills/markdown_format.md`.
- Исправление пустых callback-блоков переведено на `event.answer(notification=...)` в menu/image/Hermes; требуется ручное подтверждение Pavel.

## 2. Баги (❌)

### HIGH

1. B1 — Image flow ломается до MiniMax API.
   - `app/max/handlers/image_gen.py:73-74`: `_set(..., **extra)` передаёт kwargs в `set_state()`.
   - `app/max/state.py:14-15`: `set_state()` принимает только `data=None`.
   - Результат: `TypeError` при `image:own`; дополнительные поля flow также хранятся не в том уровне.

2. B2 — «Мои каналы» использует неверную сущность API.
   - `app/max/handlers/menu.py:277-320` вызывает `bot.get_subscriptions()`.
   - `GET /subscriptions` возвращает webhook-подписки, а не список каналов.
   - Официальный MAX changelog: после удаления `GET /chats` готового списка нет; `chat_id` нужно сохранять из `bot_added`/`bot_started` updates.

3. B3 — ImageClient неправильно классифицирует ошибки для retry.
   - `app/llm/image_client.py:37`: retry включает 1004 auth, 1008 balance, 1026 content, 2013 params, 2049 key — эти ошибки не станут успешными после повтора.
   - `tests/test_image_client.py:195-216` закрепляет неправильное поведение.

4. B4 — ImageClient не выполняет требуемое диагностическое логирование.
   - `app/llm/image_client.py:183-238`: нет записи attempt/status_code/request id/body excerpt.
   - Таймаут конструктора по умолчанию 120с (`:80`) не берётся из `settings.image_request_timeout_s`.

5. B5 — подтверждённый plain-text fallback применён не везде.
   - `app/max/formatting.py:116`: MarkdownSender по умолчанию отправляет `Format.MARKDOWN`.
   - `app/max/handlers/start.py:26-38`: прямой `format="markdown"`.
   - `app/max/descriptions.py` и `app/max/handlers/hermes_button.py` содержат пользовательские `**...**`, backticks и markdown-ссылки.
   - `app/hermes/dispatcher.py:135-144` также формирует markdown-литералы.

6. Hermes subprocess небезопасен и оставляет transport warnings.
   - `app/hermes/session.py:166-173`: пользовательская задача вставляется в `create_subprocess_shell()`.
   - `wait()` при timeout не гарантирует остановку worker/subprocess (`:125-142,259-264`).
   - `HermesDispatcher` не хранит supervisor tasks и не имеет `aclose()`: `app/hermes/dispatcher.py:108-110`.

### MEDIUM

- `app/max/handlers/callback_handler.py` использует `logger.warning`, но logger не объявлен.
- `app/llm/image_client.py` и `image_gen.py` допускают `5:4`/`4:5`, которых нет в актуальной документации image-01.
- `_USER_MESSAGES["TIMEOUT"]` говорит 60с при фактическом лимите 120с.
- `ImageClient` создаётся на каждый generation и не закрывается в `image_gen.py`, что может оставлять HTTP-клиенты.
- `app/main.py:80` только комментирует secret validation; фактической проверки `X-Max-Bot-Api-Secret` нет.
- Docker compose не фиксирует production webhook mode; по умолчанию `max_use_polling=True`.
- В `keyboards.py`, `menu.py`, handlers остались устаревшие импорты/докстринги про `callback_message()`.
- `requirements.txt` не фиксирует верхние/точные версии; fresh Docker может получить несовместимый major SDK.

### LOW

- Смешаны русские и английские докстринги/комментарии.
- Много крупного handler-кода (`image_gen.py` 521 строка, `executors.py` 491 строка).
- Старые V2/V3 комментарии противоречат фактическому plain-text поведению.
- `callback_message()` в `keyboards.py` после V5 почти не нужен.

## 3. Не сделано (⚠️, из HANDOFF)

- `group_listen.py`: регистрация `bot_added`/group commands/mentions и каталог известных каналов.
- `image_handler.py`: входящие изображения → MiniMax-M3 vision.
- Реальный antispam middleware (есть только таблицы).
- Сертификаты Минцифры в Docker.
- Webhook secret validation и production E2E.
- Реальный ручной тест каждого сценария Pavel в MAX.

В текущей задаче обязательно закрываются сертификаты и минимальный channel registry, необходимый для B2. Group mentions, vision и antispam остаются отдельным scope, если не требуются для acceptance.

## 4. Технический долг

- Неверный abstraction boundary: webhook subscriptions выдаются за channel directory.
- `MarkdownSender` по названию и поведению противоречит принятому plain-text UX.
- Одноразовые ImageClient создают сетевые пулы в handler, вместо shared context/гарантированного `aclose()`.
- Hermes worker создаёт второй Orchestrator/LLMClient на каждую задачу и затем снова пытается Hermes через Orchestrator.
- Нет централизованного callback acknowledgement helper для всех handlers.
- Нет регрессионных тестов на реальные callback paths (image own/aspect, notification without bubble).
- Нет таблицы known_chats/channel registry.

## 5. Архитектурные проблемы

- Async lifecycle: Hermes subprocess и supervisor tasks не принадлежат lifespan shutdown.
- Shell injection: task конкатенируется в shell command.
- Event routing: несколько широких `@dp.message_callback()` и `@dp.message_created()` зависят от порядка регистрации.
- State schema не типизирована и уже разошлась между `state.py` и `image_gen.py`.
- Production transport: polling и webhook нельзя использовать одновременно, но deployment policy не зафиксирована.
- Webhook endpoint не проверяет secret и выполняет dispatcher inline до ответа.

## 6. Последовательность исправлений

### Фаза 2 — B1–B5

1. RED-тесты: image FSM data, non-retryable MiniMax errors, plain sender, channel registry, callback no-bubble.
2. Исправить state contract и image callback path.
3. Исправить retry/logging/timeouts/aspect list и client close.
4. Добавить `known_chats` в SQLite и handler событий `bot_added`/`bot_removed`; «Мои каналы» читает registry.
5. Перевести весь user-facing output на plain text; API `format` не передавать.
6. Проверить `set_commands()` и зарегистрированные 11 handlers/commands через SDK mock + startup log.

### Фаза 3–4 — Hermes

1. Убрать shell-конкатенацию; запускать argv через `create_subprocess_exec`.
2. При timeout/cancel закрывать process tree/worker.
3. Хранить supervisor tasks и закрывать их в `HermesDispatcher.aclose()` из app lifespan.
4. Проверить 3 сценария и plain output.

### Фаза 5–6 — промпты/UI

1. Проверить все 8 system prompts: plain-text skill, anti-AI стоп-лист, B2B tone.
2. Удалить markdown из descriptions/start/Hermes/image copy.
3. Перевести остатки user-facing English.

### Фаза 7 — сертификаты

1. Скачать сертификаты только с `https://www.gosuslugi.ru/crt` или официальных ссылок страницы.
2. Сохранить в `certs/`, записать SHA-256 и subject/issuer.
3. Dockerfile: установить `ca-certificates`, COPY certs, `update-ca-certificates`.
4. Собрать image и проверить TLS к `platform-api2.max.ru` внутри контейнера.

### Фаза 8 — acceptance

- compileall/py_compile: чисто.
- pytest: минимум 105 passed, 0 resource warnings по Hermes.
- Убить текущий 8080, поднять uvicorn, проверить `/health`, startup log, 17+ handlers, 11 commands.
- Smoke MiniMax image endpoint с ключом из `.env`: логировать только status/request id, никогда токен/body prompt целиком.
- Ручной MAX checklist остаётся Pavel: `/start`, `/`, image flow, channels, manual post, copy/plain, Hermes.

## 7. Жёсткие ограничения

- Не изменять `.env`, `app/max/bot_wrapper.py`, имена полей `config.py`.
- Не добавлять LangChain/LiteLLM/Redis/PostgreSQL/aiogram/Telegram SDK/FSM framework/anthropic SDK.
- Только `platform-api2.max.ru`, токен только Authorization header.
- Никаких придуманных секретов.
- Никакого markdown в MAX user-facing output.
- Image prompt ≤1500, `n=1`, URL скачивать сразу.
- Не объявлять ручной MAX UI acceptance пройденным без подтверждения Pavel.
