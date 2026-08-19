# Hermes user stories → skills/tools — R4 (шляпа GZA, 2026-08-19)

Источник: https://hermes-agent.nousresearch.com/docs/user-stories

## Фильтр

**Отбросил:**
- Уже дублируют наши команды: research, copy, plan, code_exec
- Требуют ключей, которых у нас нет: Telegram bot token, Twitter API, Polymarket wallet, OpenAI/Anthropic direct (у нас только MiniMax), SearXNG container
- Требуют специфической инфраструктуры: Nextcloud, LibreOffice, Supabase CRM, Kubernetes deployment
- Слишком узкие: bed-time stories, B1 battle droid skin, Turkish locale

**Оставил кандидатами (что полезно для MAX-бота Павла — маркетинг/аналит/конт):**
1. **Voice input** (Discord: "i cant type to well so being able to use voice from terminal window is huge") — voice-to-text в MAX.
2. **Converse mode** (Discord: "agent thinks before it acts") — чат с подтверждением перед действием.
3. **HTML landing page generator** (X: "told it to Google me and then build a landing page") — LLM пишет HTML.

## Вердикт по каждому

### 1. Voice input — ❌ СКИП
- Причина: нужна интеграция с MAX voice API (которого нет, только текст). Плюс whisper.cpp install. Pavel не давал voice-API ключ.

### 2. Converse mode — ❌ СКИП
- Причина: у нас уже есть кнопочный flow через inline-keyboard (см. `/copy` → подсказка → ввод → результат). Converse mode — это другой паттерн (chat-first), а Pavel хочет кнопочный. Дублирует существующий функционал.

### 3. HTML landing page generator — ⚠️ ЧАСТИЧНО ПОДХОДИТ
- Что: LLM пишет готовый HTML по описанию бизнеса.
- Уже есть: у нас нет такого. `/copy` пишет текстовые посты, не HTML.
- Проблема: HTML нужно куда-то сохранять (файл в `data/landing_pages/`), а Pavel не упоминал хостинг.
- **Решение: не реализую как отдельный tool сейчас.** Можно добавить позже, когда появится требование.

## Топ-3 альтернатива из ВНУТРЕННИХ паттернов Pavel'я

Из самого нашего кода и аудита — что реально добавит ценности:

### Топ-3 #1: `/help` через MarkdownSender + START_TOUR + COMMAND_DESCRIPTIONS ✅ уже сделано в V2
### Топ-3 #2: Кнопка 🤖 Hermes в main_menu ✅ клавиатура уже есть (V3 база), handler отсутствует — это Фаза 3 (H1-H3)
### Топ-3 #3: Slash-команды через `set_commands` (не `set_my_commands`) — Фаза 2 (B1)

## Что делаю

**Никаких новых skills/tools из user stories не реализую** — все три кандидата либо скипнуты (voice/converse) либо не подходят (HTML без хостинга).

**Вместо этого** в Фазе 2 закрою B1 (правильный set_commands), в Фазе 3 реализую Hermes-кнопку из V3 базы — это и есть наш "топ-3 фич" для текущей итерации.

## Если Pavel хочет именно что-то из user stories — пусть скажет

Конкретные user-story которые я могу реализовать **сейчас** (без новых API ключей):

| User story | Что нужно | Трудозатраты |
|---|---|---|
| "Schedule task every Monday at 9am" | Cron в MAX-боте | 2-3 часа (нужен apscheduler или простой loop) |
| "Daily journaling into Obsidian" | Knowledge base по chat history | 4-6 часов (markdown vault) |
| "Build landing page from URL" | LLM + write to disk | 3-4 часа (LLM + storage) |
| "Memory wiki of past conversations" | SQL → markdown export | 2-3 часа (read storage → write files) |

Pavel — если хочешь что-то из этого списка, скажи, и я реализую в следующей фазе. Сейчас — закрываю 5 багов + Hermes-кнопку + audit.