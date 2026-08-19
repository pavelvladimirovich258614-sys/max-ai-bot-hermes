# PROMPT FOR CODEX: Full Refactor of max-ai-bot

> Скопируй ВСЁ ниже строки `=== COPY START ===` в Codex (или любой другой AI code-assistant) и получи полный аудит + план рефакторинга.

---

## === COPY START ===

### Контекст проекта

**max-ai-bot** — production Telegram-style AI-бот для мессенджера **MAX** (Российский аналог Telegram, был VK Мессенджер). Бот работает в личных чатах, группах и каналах MAX, даёт AI-сервис (research, копирайтинг, маркетинг, генерация картинок) через LLM (MiniMax-M3 primary, StepFun step-3.7-flash fallback).

**Целевая аудитория** бота — B2B-клиенты владельца: коучи, психологи, юристы, эксперты. Бот помогает им готовить контент для их каналов в MAX.

**Владелец:** Pavel (Паша), user_id 73412011 в MAX, chat_id 154939916.
**Бот:** @id752703975446_3_bot (id 224141223, MAX).
**Webhook URL:** https://max.ai-agent-paul.ru/webhook/max (поддомен `max` → 82.39.213.82).
**Production deployment:** Ubuntu VPS, Docker + uvicorn (FastAPI).

### Архитектура (прочитай ВЕСЬ проект перед началом)

```
D:\hermes-multi-agent-setup\max-ai-bot\
├── app\
│   ├── main.py                 # FastAPI app, lifespan, webhook + polling startup
│   ├── config.py               # pydantic-settings (.env loader)
│   ├── context.py              # shared app.state: bot, dispatcher, storage, llm, hermes_client
│   ├── max\
│   │   ├── client.py           # CompliantBot (обходит SDK баги: API_URL + Authorization header)
│   │   ├── keyboards.py        # 11+ inline keyboards: main_menu, post_submenu, post_approval, etc
│   │   ├── ui.py               # send_home_button, ProgressReporter, clean_for_max, attach_local_image
│   │   ├── executors.py        # run_role / do_* / с ProgressReporter + timeout 60с + retry
│   │   ├── formatting.py       # MarkdownSender (НЕ работает в MAX — fallback на plain text)
│   │   ├── descriptions.py     # COMMAND_DESCRIPTIONS (8 описаний на русском) + START_TOUR
│   │   ├── publisher.py        # publish_post / publish_with_image (для каналов)
│   │   └── handlers\
│   │       ├── start.py        # /start с баннером + 10 кнопок
│   │       ├── menu.py         # callback routing, FSM state, /help
│   │       ├── post.py         # /post + post:manual FSM + post:my_channels
│   │       ├── free_chat.py    # обычный диалог с прогрессом
│   │       ├── image_gen.py     # /image — генерация картинок через MiniMax image-01
│   │       └── callback_handler.py  # approve/reject/edit inline-кнопки
│   ├── hermes\
│   │   ├── client.py           # HTTP-клиент к RZA (Wu-Tang Hermes orchestrator)
│   │   ├── session.py          # HermesSession dataclass
│   │   └── dispatcher.py       # spawn_session + supervisor (мониторинг RZA-сессии)
│   ├── llm\
│   │   ├── client.py           # OpenAI-compatible LLM client (MiniMax Anthropic-style + StepFun OpenAI-style)
│   │   ├── image_client.py     # MiniMax image-01 генерация (text-to-image + image-to-image)
│   │   ├── prompts\            # 8 ролей: researcher, copywriter, marketer, ideator, analyzer, prompt_engineer, chat, image_prompt
│   │   └── skills\
│   │       └── markdown_format.md   # V4 skill: "MAX не рендерит markdown, используй эмодзи + ЗАГЛАВНЫЕ + • буллиты"
│   ├── tools\
│   │   ├── web_search.py       # duckduckgo_search (default, no API key)
│   │   └── web_reader.py       # httpx + trafilatura
│   ├── db\
│   │   ├── models.py           # dataclasses: User, Message, Publication, Session, GeneratedImage, HermesSession
│   │   └── storage.py          # aiosqlite CRUD
│   ├── core\
│   │   └── orchestrator.py     # _system_prompt(role) + ROLE_SKILLS dict
│   ├── middleware\
│   │   ├── auth.py             # проверка ADMIN_USER_IDS
│   │   └── rate_limit.py       # семафор 30 rps
│   └── state.py                # FSM (user_id → state dict), простой in-memory
├── tests\                      # 90 passed (pytest)
├── docs\
│   ├── HANDOFF.md              # контекст для новой сессии
│   ├── FEATURES-V2-PLAN.md     # что сделано в V2
│   ├── REFACTOR-V3-PLAN.md     # (создай) план рефакторинга V3
│   ├── CODEX-REFACTOR-PROMPT.md  # этот файл
│   └── research\               # (создай) research-документы
├── data\                       # SQLite + images (volumes)
├── logs\                       # bot_run.log (volume)
├── .env                        # реальные токены Pavel'я (НЕ ТРОГАТЬ, НЕ КОММИТИТЬ)
├── .env.example                # шаблон
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

### Стек (жёсткие требования)

- **Python 3.12+** (локально на Windows 10 у Pavel'я, 3.11 в venv)
- **maxapi-sdk** (PyPI, import path `maxapi`) — НЕ `python-telegram-bot`, НЕ `aiogram`
- **FastAPI + Uvicorn** (webhook + polling)
- **httpx** (async HTTP)
- **pydantic-settings** (env config)
- **aiosqlite** (state, history, generated images registry)
- **Docker + docker-compose** (прод-деплой)
- **БЕЗ** LangChain, LiteLLM, Redis, PostgreSQL, FSM-фреймворков, anthropic SDK
- **БЕЗ** изменений в `.env`, `bot_wrapper.py`, `config.py` (имена полей)

### LLM провайдеры (НЕ менять)

- **Primary:** MiniMax-M3 через Anthropic-compatible endpoint
  - Base URL: `https://api.minimax.io/anthropic` (international, НЕ China `api.minimaxi.com`)
  - Auth: `x-api-key: $LLM_PRIMARY_API_KEY` + `anthropic-version: 2023-06-01`
  - Endpoint для вызова: `POST {base}/v1/messages` (использует Messages API формат)
- **Fallback:** StepFun step-3.7-flash через OpenAI-compatible
  - Base URL: `https://api.stepfun.ai/v1` (global, НЕ China)
  - Auth: `Authorization: Bearer $LLM_FALLBACK_API_KEY`
- **MiniMax image-01** (для картинок) — `POST https://api.minimax.io/v1/image_generation` с тем же Bearer ключом
- **НЕТ** ключей OpenAI или оригинального Anthropic

### MAX Bot API (актуально на 18.08.2026)

- **Base URL:** `https://platform-api2.max.ru` (НЕ `botapi.max.ru` — deprecated)
- **Auth:** `Authorization: <token>` header (НЕ query-параметр)
- **Webhook:** только HTTPS + сертификат доверенного CA (Минцифры, Let's Encrypt)
- **Polling:** для dev
- **Rate limit:** 30 rps
- **GET /chats удалён** с июня 2026, используй `POST /subscriptions`
- **Команды бота** управляются через `PATCH /me/commands`
- **Markdown НЕ рендерится в MAX UI** для текущего бота — Pavel подтвердил скриншотами 2026-08-19, format=markdown игнорируется. **Используй plain-text с эмодзи-маркерами.**
- **Сертификат Минцифры в Docker** — прод-блокер, НЕ решено

### Что работает (✅)

| Фича | Файл | Статус |
|------|------|--------|
| Бот стартует в polling на 8080 | main.py | ✅ |
| 11 slash-команд зарегистрированы | client.py | ✅ V4 |
| /start с баннером + 10 кнопок | start.py | ✅ |
| Inline-кнопки меню | keyboards.py | ✅ |
| ProgressReporter | ui.py | ✅ |
| Timeout 60с + try/except в executor | executors.py | ✅ |
| send_home_button с непустым текстом | ui.py | ✅ V4 |
| clean_for_max убирает markdown-артефакты | ui.py | ✅ V4 |
| Markdown skill (plain-text fallback) | llm/skills/markdown_format.md | ✅ V4 |
| 8 ролей с TONE OF VOICE | llm/prompts/*.py | ✅ |
| Генерация картинок через MiniMax image-01 | llm/image_client.py | ✅ |
| FSM для post:manual | handlers/post.py | ✅ V4 |
| Кнопка [❌ Отмена] в FSM-подсказках | handlers/menu.py | ✅ V4 |
| py_compile чистый | — | ✅ |
| 90/90 pytest passed | tests/ | ✅ |

### Что НЕ работает (❌) — 5 багов Pavel'я

| # | Баг | Файл | Что нужно |
|---|-----|------|-----------|
| 1 | Кнопка "🎨 Сгенерировать картинку" не работает | handlers/image_gen.py | Проверить почему MiniMax не отвечает, обновить endpoint если устарел, добавить детальное логирование |
| 2 | "Мои каналы" возвращает пусто | handlers/menu.py | Найти правильный API вызов (bot.get_subscriptions / bot.subscriptions / bot.api.getSubscriptions), retry через 3 сек, user-friendly подсказка |
| 3 | "Ввести chat_id вручную" сразу кидает в меню | handlers/menu.py | FSM state не ставится (УЖЕ В V4 сделан `post:awaiting` — но возможно Pavel не нажимал кнопку, проверь работу) |
| 4 | Markdown рендерится как литералы | formatting.py + llm/prompts | MAX не рендерит — FALLBACK на plain text (СДЕЛАНО в V4 — но убедись что чистый вывод) |
| 5 | Slash-команды не зарегистрированы | client.py | УЖЕ В V4 (11 команд через set_my_commands) — но проверь что РЕАЛЬНО работает в MAX UI |

### Что НЕ сделано (⚠️) — из HANDOFF

| Проблема | Где | Что делать |
|----------|-----|------------|
| Кнопка 🤖 Hermes в main_menu | keyboards.py + handlers/hermes_button.py | Sub-agent, спавнит RZA-сессию из MAX |
| Group/channels listening (`group_listen.py`) | не создан | Реагировать на /команды в группах |
| Image vision (приём картинок от юзера) | не создан | image_handler.py — LLM vision через MiniMax-M3 |
| Antispam (middleware) | только таблицы | Реальный handler + middleware |
| Сертификат Минцифры в Dockerfile | Dockerfile | Добавить CA в образ |
| Webhook (вместо polling) | main.py | Не тестирован, нужны сертификаты |
| Аудит кода (мёртвый код, TODO, хардкод) | app/ | Систематический проход |

### Репо и документация (все ссылки)

**Репозиторий Pavel'я:**
- https://github.com/pavelvladimirovich258614-sys/max_hermes_agent_new (если 404 — спросить Pavel'я)

**MAX Bot API документация:**
- https://dev.max.ru/docs — главная
- https://dev.max.ru/docs-api — API reference
- https://dev.max.ru/docs-api/changelog-api — changelog (критичные изменения)
- https://dev.max.ru/docs/chatbots/bots-coding/prepare — сценарии работы
- https://dev.max.ru/docs/chatbots/bots-coding/js — форматирование (markdown НЕ рендерится)
- https://dev.max.ru/docs/chatbots/bots-nocode/create — создание бота
- https://platform-api2.max.ru — production API

**MAX SDK:**
- https://github.com/max-messenger/max-botapi-python — основной SDK (от MAX команды)
- https://pypi.org/project/maxapi/ — PyPI страница
- https://pypi.org/project/maxapi-sdk/ — production-ready версия (используем)
- https://github.com/MaxApiDevs/max-bot-aio-template — production scaffold
- https://github.com/max-messenger/max-bot-api-client-ts — TypeScript клиент
- https://github.com/green-api/maxbot-api-client-python — green-api клиент
- https://github.com/green-api/maxbot-chatbot-python — chatbot framework
- https://pypi.org/project/python-max-bot/ — thin Pydantic wrapper
- https://pypi.org/project/maxapi-python/ — WebSocket-based (pymax)
- https://libraries.io/pypi/python-max-bot — описание
- https://www.piwheels.org/project/maxapi-sdk/ — RPi wheels

**MAX дополнительные источники:**
- https://dev.to/githubopensource/pymax-unleash-the-power-of-max-messenger-with-this-async-python-wrapper-1ldm — pymax review
- https://habr.com/ru/articles/1005282/ — реальные payload'ы MAX (отличия от доков)
- https://habr.com/ru/articles/951326/ — изменение правил MAX (только юрлица РФ)
- https://flaton.systems/blog/messengers/max-mini-prilozheniya-boty-i-funktsii-rossiyskoy-platformy — обзор MAX
- https://nbmit.ru/blog/dev/max-bot-api-migration-platform-api2-july-2026 — миграция на platform-api2
- https://www.m24.ru/news/07082026/928269 — MAX открыл API для альтернативных клиентов
- https://searx.space — агрегатор публичных SearXNG

**MiniMax (LLM + image):**
- https://platform.minimax.io/docs — главная
- https://platform.minimax.io/docs/guides/image-generation — image guide
- https://platform.minimax.io/docs/api-reference/image-generation-t2i — text-to-image API
- https://platform.minimax.io/docs/api-reference/image-generation-i2i — image-to-image API
- https://platform.minimax.io/docs/api-reference/text-anthropic-api — Anthropic-compatible
- https://platform.minimax.io/docs/token-plan/other-tools — OpenAI/Anthropic совместимость
- https://platform.minimax.io/user-center/payment/token-plan — API key
- https://platform.minimax.io/console/usage — usage console
- https://minimax-ai.chat/docs/api/ — MiniMax API guide 2026
- https://minimax-ai.chat/docs/minimax-api-key-base-url/ — base URLs по регионам
- https://minimax-ai.chat/docs/anthropic-compatible-api/ — Anthropic-совместимый API

**StepFun (fallback LLM):**
- https://platform.stepfun.ai/docs/en/api-reference/models/list
- https://platform.stepfun.ai/docs/en/step-plan/quick-start
- https://platform.stepfun.ai/docs/zh/api-reference/models/list
- https://platform.stepfun.ai/docs/zh/step-plan/integrations/reasoning-api

**Hermes Agent (NousResearch — для интеграции):**
- https://hermes-agent.nousresearch.com/docs/user-stories
- https://github.com/NousResearch/hermes-agent

**Web search alternatives (Pavel предпочитает API-free):**
- https://github.com/D4Vinci/Scrapling — Python parser
- https://github.com/apify/crawlee — Node scraper
- https://github.com/searxng/searxng — self-hosted search
- https://github.com/benbusby/whoogle-search — Google proxy
- https://github.com/hnhx/librex — LibreX meta-search
- https://github.com/deedy5/duckduckgo_search — pip пакет
- https://searx.space — агрегатор публичных

**Telegram / Teletype (смежные проекты Pavel'я):**
- https://t.me/Novopoltsev_Pavel — Telegram канал Pavel'я
- https://teletype.in/@rovniy_paha — блог Pavel'я (181 пост)

### Задача для рефакторинга

#### Фаза 1: Аудит (1 час)

Прочитай ВСЕ файлы проекта. Создай `docs/REFACTOR-V3-PLAN.md` со следующими секциями:

1. **Состояние (✅)** — что работает, с file:line
2. **Баги (❌)** — что сломано, с file:line + severity (high/medium/low)
3. **Не сделано (⚠️)** — из HANDOFF.md
4. **Технический долг** — мёртвый код, TODO, хардкод, дублирование
5. **Архитектурные проблемы** — цикл. импорты, threading, async-issues

#### Фаза 2: Фикс 5 багов (2-3 часа)

- **Баг 1 (image gen):** Проверь `app/llm/image_client.py`, smoke-curl к MiniMax API. Обнови endpoint если устарел. Добавь retry с exponential backoff. Логируй каждый запрос с status_code + body excerpt.
- **Баг 2 (my channels):** Найди в исходниках maxapi правильный метод получения каналов/чатов бота (возможно `await bot.api.getSubscriptions()` или `bot.subscriptions()`). Если возвращает [] — добавь retry + user-friendly сообщение.
- **Баг 3 (post:manual FSM):** Уже V4 сделал — проверь что state 'post:awaiting' реально ставится и обрабатывается.
- **Баг 4 (markdown):** V4 сделал fallback — убедись что clean_for_max() вызывается везде.
- **Баг 5 (slash-commands):** V4 сделал set_my_commands — проверь что MAX реально показывает подсказки.

#### Фаза 3: Hermes integration (2-3 часа)

- **Кнопка 🤖 Hermes в main_menu** (keyboards.py) — добавь 10-ю кнопку
- **app/hermes/session.py** — HermesSession dataclass
- **app/hermes/dispatcher.py** — spawn_session() через subprocess/hermes-cli/hermes peer dm, ProgressReporter каждые 30с, таймаут 5 мин
- **app/max/handlers/hermes_button.py** — callback_data='hermes' → подменю [Контент-план / Ресёрч / Произвольная задача / В меню]
- **storage.py** — таблица hermes_sessions

#### Фаза 4: Кнопка 🤖 Hermes use-cases

- "Контент-план" → spawn_session(role='marketer', task=тема)
- "Ресёрч" → spawn_session(role='researcher', task=тема)
- "Произвольная задача" → spawn_session(role='chat', task=текст)

#### Фаза 5: Полировка промптов

Все 8 промптов в `app/llm/prompts/`:
- Содержат skill markdown_format
- Anti-AI-isms стоп-лист (delve/leverage/unlock/...)
- TONE OF VOICE (B2B для коучей/психологов/юристов)
- Структура через эмодзи (▶ Заголовок, ✅, ⚠️, 💡, •)

#### Фаза 6: Редактура

- descriptions.py — 8 описаний на натуральном русском
- keyboards.py — подписи кнопок
- callback_handler.py — approve/reject/edit тексты
- start.py — дружелюбное приветствие + START_TOUR
- Найти английский в user-facing — перевести

#### Фаза 7: Сертификат Минцифры (прод-блокер)

- Скачай CA с https://www.gosuslugi.ru/crt
- Добавь в Dockerfile: `ADD ./certs/minstroy_ca.crt /usr/local/share/ca-certificates/`
- `RUN update-ca-certificates`
- Альтернативно: openssl verify против platform-api2.max.ru — какие CA нужны

#### Фаза 8: Acceptance

1. `py_compile` всех файлов — чисто
2. `pytest -q` — все 90 предыдущих + новые тесты (фаза 2-7) = минимум 105 passed
3. PowerShell kill → uvicorn → лог: "Application startup complete" + "Бот: @id..." + 17+ хендлеров
4. Ручной тест Pavel'я каждого бага + каждой новой фичи
5. Финальный отчёт в формате:

```
## Рефакторинг V3 — финальный отчёт

### Что закрыто
- [✅/❌] B1-B5 (5 багов)
- [✅/❌] H1-H3 (Hermes integration)
- [✅/❌] F1-F3 (фаза 5-7)
- [✅/❌] Сертификат Минцифры в Docker

### Тесты
- pytest: X passed
- py_compile: чисто

### Новые файлы
- [список с file:line]

### Изменённые файлы
- [список с кратким описанием]

### Что осталось
- [список]
```

### Жёсткие правила (Чего НЕ делать)

- **НЕ выдумывать токены/ключи** — бери только из `.env` или сессии Pavel'я
- **НЕ менять `.env`, `bot_wrapper.py` (домен v2 + Authorization), `config.py` (имена полей)**
- **НЕ добавлять LangChain, LiteLLM, Redis, PostgreSQL, FSM-фреймворки, anthropic SDK**
- **НЕ использовать Telegram SDK** (aiogram, python-telegram-bot) — это MAX
- **НЕ использовать старый MAX домен `botapi.max.ru`** или `platform-api.max.ru` — только `platform-api2.max.ru`
- **НЕ передавать токен в query-параметре** — только `Authorization` header
- **НЕ использовать HTTP webhook** — только HTTPS
- **НЕ симулировать "параллельных sub-агентов"** через hermes peer dm / канбан — Pavel знает что это не работает
- **НЕ использовать markdown в выводе** — MAX не рендерит, используй plain text + эмодзи
- **НЕ использовать `format=markdown`** в MAX API — игнорируется
- **НЕ генерировать картинки с промптом > 1500 символов** (лимит MiniMax API)
- **НЕ использовать `n>1`** в image_generation (для скорости)
- **НЕ хранить URL от MiniMax дольше сессии** — скачивай сразу (протухает за 24ч)
- **НЕ отвечать "сделано" пока не проверишь КАЖДЫЙ пункт руками**

### Технические хитрости (HANDOFF.md)

1. **Убить uvicorn на Windows:** `Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
2. **MAX не рендерит markdown** — весь вывод через `clean_for_max()` (строки в `app/max/ui.py`)
3. **`/start` ломается при IndentationError** — patch tool иногда ломает отступы, для таких файлов `write_file` с полным содержимым
4. **`event.message.answer()` поддерживает `format=Format.MARKDOWN`**, но MAX UI игнорирует

### Начни с

1. Прочитай `docs/HANDOFF.md`
2. Прочитай `docs/FEATURES-V2-PLAN.md`
3. Прочитай ВСЕ файлы в `app/` (tree + ключевые модули, не весь код)
4. Сделай аудит
5. Создай `docs/REFACTOR-V3-PLAN.md`
6. По плану — фиксы по фазам

Когда закончишь — дай финальный отчёт в формате выше.

## === COPY END ===
