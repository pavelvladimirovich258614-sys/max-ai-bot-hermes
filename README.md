# MAX AI Bot (Hermes Edition)

AI-бот для мессенджера MAX. Помогает B2B-экспертам — коучам, психологам, юристам и консультантам — готовить контент для каналов MAX.

> **Безопасность:** реальные ключи и токены живут только в локальном `.env`. Файл игнорируется Git и никогда не должен попадать в репозиторий.

## Возможности

- 🔍 **Research** — разбор темы с фактами и источниками
- ✍️ **Copy** — варианты продающего поста
- 📅 **Plan** — контент-план на N дней
- 📤 **Post** — preview и публикация в канал MAX
- 🔬 **Analyze** — анализ URL или статьи
- 💡 **Ideate** — идеи для контента
- 🎯 **Prompt** — помощь с промптами
- 🎨 **Image** — MiniMax image-01: свой промпт или visual из текста поста
- 🤖 **Hermes** — запуск локальной Hermes-сессии из MAX
- 🔄 **Restart** — возврат к стартовому меню

Доступны 11 slash-команд: `/start`, `/help`, `/research`, `/copy`, `/plan`, `/post`, `/analyze`, `/ideate`, `/prompt`, `/image`, `/restart`.

## Стек

- **Python 3.12+**, **FastAPI**, **Uvicorn**
- **maxapi** — Python SDK для MAX Bot API
- **MiniMax-M3** (Anthropic-compatible) с fallback на **StepFun step-3.7-flash**
- **MiniMax image-01** для изображений
- **aiosqlite** для state и истории
- **Docker** для деплоя
- Без LangChain, LiteLLM, Redis и PostgreSQL

## Структура

```text
app/
├── main.py              # FastAPI app и lifecycle
├── config.py            # pydantic-settings
├── max/                 # MAX client, keyboards, handlers, publisher
├── llm/                 # LLM client, 8 role-prompts, image client
├── hermes/              # Hermes CLI / session integration
├── tools/               # web_search, web_reader
├── db/                  # SQLite storage
├── core/                # role routing orchestrator
└── middleware/          # auth и rate-limit
docs/                    # handoff, планы и операционные документы
tests/                   # pytest regression suite
```

## Быстрый старт (локально)

```bash
git clone https://github.com/pavelvladimirovich258614-sys/max-ai-bot-hermes.git
cd max-ai-bot-hermes
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
copy .env.example .env   # Windows
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Заполни `.env` реальными значениями:

```env
MAX_BOT_TOKEN=<YOUR_MAX_BOT_TOKEN>
LLM_PRIMARY_API_KEY=<YOUR_MINIMAX_API_KEY>
LLM_FALLBACK_API_KEY=<YOUR_STEPFUN_API_KEY>
MAX_ADMIN_USER_IDS=<YOUR_MAX_USER_ID>
MAX_WEBHOOK_URL=https://your-domain.example/webhook/max
MAX_USE_POLLING=true
```

В MAX найди бота и отправь `/start`.

## Изображения из поста

Кнопка **🎨 Сгенерировать картинку → 🤖 Из поста** по умолчанию создаёт широкий 16:9 professional editorial visual, отражающий главную мысль поста. В конце текста можно добавить:

```text
Пожелания: тёплый свет, без людей, premium editorial style
Формат: 9:16
```

Пожелания имеют приоритет, а `Формат:` переопределяет дефолт 16:9.

## Production

См. [docs/CODEX-DEPLOY-PROMPT.md](docs/CODEX-DEPLOY-PROMPT.md). Продакшен использует HTTPS webhook, Docker и доверенную CA-цепочку. Локальная разработка использует long polling.

## Документация

- [Handoff](docs/HANDOFF.md)
- [V2 features](docs/FEATURES-V2-PLAN.md)
- [V3 refactor plan](docs/REFACTOR-V3-PLAN.md)
- [V3 refactor report](docs/REFACTOR-V3-REPORT.md)
- [Manual test cases](docs/TEST-CASES.md)

## Безопасность

- `.env` игнорируется Git.
- Реальные секреты не хранятся в коде или документации.
- `CompliantBot` использует `platform-api2.max.ru` и заголовок `Authorization`.
- Для production webhook требуется HTTPS и доверенный сертификат.
- Ограничение запросов к MAX: 30 rps.

## Лицензия

Proprietary. © 2026 Pavel Novopoltsev.
