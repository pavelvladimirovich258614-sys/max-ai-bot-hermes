# Hermes integration design — реализация кнопки [🤖 Hermes]

**Дата:** 2026-08-19
**Автор:** RZA-исследователь (sub-agent)
**Контекст:** REFACTOR-V3-PLAN, фаза C — кнопка [🤖 Hermes] в MAX-боте
**Цель:** конкретный план реализации трёх вариантов интеграции с trade-offs

---

## TL;DR — Рекомендация

**Вариант B (In-process dispatcher) + строить фундамент под A.**

- 6-8 часов работы
- 95% совместимости с текущим `app/hermes/client.py`
- Не требует настройки peer/registry в Pavel's окружении
- Задел на будущий переход к A (когда Pavel настроит peer)

---

## 1. Текущее состояние (что есть СЕЙЧАС)

### 1.1 Наш код — `max-ai-bot/`

**Точка входа Hermes:** `app/hermes/client.py` (109 строк)
- Класс `HermesClient` с двумя транспортами:
  - **HTTP**: `POST {HERMES_RZA_URL}` (default: `http://host.docker.internal:9119/api/hermes/route`)
  - **CLI**: `asyncio.create_subprocess_shell(f'{hermes_rza_cli} "{task}"')` (default: `hermes peer dm rza`)
- `HERMES_MODE=auto` — пробует HTTP, при ошибке падает на CLI
- Возвращает `None` если всё упало

**Оркестратор:** `app/core/orchestrator.py` (151 строка)
- `Orchestrator.run()` → `HermesClient.route()` → если `None`, то `LLMClient.chat()` (fallback)
- То есть **Hermes-отказ БОЛЬШЕ НЕ ПАРАЛИЗУЕТ бота** — LLM спасёт

**Состояние кнопки [🤖 Hermes]:**
- `app/max/keyboards.py:38` — кнопка **уже добавлена** в `main_menu_keyboard()`
- `app/max/keyboards.py:44-53` — `hermes_submenu_keyboard()` **уже добавлена** с 3 сценариями (`hermes:plan`, `hermes:research`, `hermes:custom`)
- `app/db/storage.py:112-124` — таблица `hermes_sessions` **уже создана** (id, user_id, chat_id, role, task, scenario, status, progress_json, result_text, created_at, finished_at)
- `app/db/storage.py:245-294` — CRUD-методы `create_hermes_session`, `update_hermes_session_progress`, `finish_hermes_session`, `get_hermes_session` **уже есть**

**Что НЕ реализовано** (см. `logs/bot_run.log` + REFACTOR-V3-PLAN §фаза C):
- `app/hermes/session.py` — **отсутствует**
- `app/hermes/dispatcher.py` — **отсутствует**
- `app/max/handlers/hermes_button.py` — **отсутствует** (callback `hermes` сейчас молча проглатывается в `menu.py:71-72` — там только `image:*` skip)
- Регистрация в `app/max/client.py` — **отсутствует**

### 1.2 Логи подтверждают отказы

```
2026-08-19 10:04:43,214 WARNING maxbot.hermes: Hermes HTTP error: All connection attempts failed
2026-08-19 10:04:43,214 INFO maxbot.hermes: Hermes HTTP failed; trying CLI
2026-08-19 10:04:44,381 WARNING maxbot.hermes: Hermes CLI rc=1 err=b"No peer named 'rza'. Run: hermes peer list\r\n"
2026-08-19 10:04:44,381 INFO maxbot.orchestrator: orchestrate role=copywriter via=llm-fallback
```

**Оба транспорта падают прямо сейчас.** LLM-fallback спасает, но кнопка [🤖 Hermes] должна явно показывать что Hermes недоступен, а не превращаться в копию [✍️ Копирайтинг].

### 1.3 Pavel's репозиторий — `max_hermes_agent_new/`

**Структура:**
```
plugin/max/
├── __init__.py            (9 строк — register())
├── adapter.py             (1804 строк, 76 KB — основной плагин)
├── plugin.yaml            (манифест, 30 строк)
├── role_registry.yaml     (82 строки — /copy, /dev, /marketing, /prompt)
├── role_registry.example.yaml
├── team_manager/
│   ├── core.py            (669 строк — /team-add validation + pending state)
│   └── __init__.py
└── tests/                 (1758 строк test_adapter.py)
```

**Это НЕ альтернативный бот — это GATEWAY PLUGIN для `hermes-agent` (наш основной сеанс!).**

| Аспект | Pavel's подход | Наш подход |
|---|---|---|
| Где живёт | `~/.hermes/plugins/max/` (gateway) | `app/` (FastAPI max-ai-bot) |
| Запуск | `hermes gateway run` | `uvicorn app.main:app` |
| Long polling | `adapter.py:1204-1575` (`MAX_POLL_TIMEOUT=30s`) | `bot.start_polling()` (maxapi SDK) |
| Worker для ролей | `role_registry.yaml` + `channel_prompt` injection | `app/core/orchestrator.py` + `app/llm/prompts/*.py` |
| LLM-вызов | Ralph/RZA в основном сеансе | `miniMax-M3` через Anthropic API |
| Команды внутри | `/copy`, `/dev`, `/marketing`, `/prompt` (slash) | `callback_data='hermes'` (inline кнопки) |
| Роль SOUL.md | Copywriter-Agent через `channel_prompt` (пояс ниже) | `app/llm/prompts/copywriter.py` (Python-модуль) |

**Ключевой механизм Pavel's — `channel_prompt` injection** (`adapter.py:1452-1461`):
```python
self._role_channel_prompt = (
    f"Ты сейчас выступаешь в роли: {role_name} ({role_desc}).\n\n"
    f"--- НАЧАЛО SOUL.md ---\n{soul_content}\n--- КОНЕЦ SOUL.md ---\n\n"
    f"Следуй своей роли. Отвечай в стиле {role_name}."
)
# → injected into MessageEvent (line 1552)
# → runner merges into agent's ephemeral system prompt
# → "the agent 'becomes' that role for this message only"
```

Это **не** subprocess-вызов. Это **in-process дополнение system prompt** в той же LLM-сессии. У нас в `app/llm/prompts/` уже есть системы промптов через `ROLE_SYSTEM_MODULES` (`orchestrator.py:29-38`), но они жёстко зашиты — нет механизма dynamic injection.

---

## 2. Что предлагает Pavel's репозиторий

### 2.1 Полезные паттерны

**A. SOUL.md как data-слой** (`examples/profiles/copywriter/SOUL.md` — 84 строки):
- Структура: `роль → задачи → стиль → запреты → когда звать других агентов`
- Можно положить в `app/hermes/souls/` и подключить динамически
- Размер: 3-6 KB на роль — копейки

**B. role_registry.yaml как маршрутизатор** (`plugin/max/role_registry.yaml`):
- `command: "/copy"` → `profile: "copywriter"` → `soul_path: "~/.hermes/profiles/copywriter/SOUL.md"`
- Mapping: текст → роль → конфиг. Чисто data, без кода
- У нас уже 3 сценария жёстко зашиты в `keyboards.py` — Pavel's подход позволит добавлять сценарии через YAML без деплоя

**C. `_load_role_registry()` + `_ROLE_COMMANDS` dict** (`adapter.py:281-302`):
- Lazy-load один раз, кэшируется в `dict[str, dict]`
- Если `enabled: false` — игнорируется. Идеально для фичефлагов
- Использует `yaml.safe_load`, ~10 строк кода

**D. `_build_role_no_task_response()`** (`adapter.py:321-331`):
- Бот сам подсказывает формат: `/copy <задача> — пиши конкретный пример`
- Почти as-is подходит для нашего `hermes:plan` без задачи

**E. `_get_soul_content()`** (`adapter.py:305-318`):
- Читает SOUL.md, проверяет существование, returns None на failure
- Без try/except в бизнес-логике — обёртка на стороне reader

**F. team_manager pattern** (`plugin/max/team_manager/core.py`):
- `/team-add name=dev route=/dev SOUL:...` — пользователь сам создаёт роль
- 669 строк, валидация (regex, секреты, занятые routes), pending state в JSON
- **Избыточно для нашей фазы C** — но если захотим дать юзеру создавать своих агентов без деплоя, вот готовый паттерн

**G. `_MODE_RU`/`_MENU_RU`/`_ROLES_RU`** (`adapter.py:182-247`):
- Текстовые help-блоки на русском, структурированные emoji
- Хороший шаблон для нашей `_HERMES_RU` (submenu description)

**H. Russian-стиль текстов (важно!)** — Pavel пишет короткими фразами:
> «Напиши задачу после команды. Пример: /copy пост про кофейню»

### 2.2 Что НЕ подходит (out of scope)

- **Весь `BasePlatformAdapter` + `MessageEvent`** — это gateway-runtime контракт, нерелевантно для FastAPI-бота
- `wants_progress_append: bool = True` (строка 570) — это хак для MAX edit_message, у нас уже есть `ProgressReporter` (`app/max/ui.py:167-310`)
- `/team-add` workflow — фича Pavel's, не нужна сейчас
- `MAX_DEDUP_*` / marker persistence — наш `maxapi` SDK уже дедуплицирует

---

## 3. Три варианта реализации кнопки [🤖 Hermes]

### Вариант A: Subprocess через `hermes peer dm rza`

**Что:** используем существующий `HermesClient._route_cli()` без изменений, в новом handler'е `hermes_button.py` запускаем его как `await deps.hermes.session(...)` (новая обёртка).

**Код (skeleton):**
```python
# app/hermes/session.py (новый)
class HermesSession:
    def __init__(self, chat_id, user_id, role, task, scenario):
        self.session_id = None  # assigned by DB
        self.role = role
        self.task = task
        self.scenario = scenario
        self.status = "running"
        self.progress: list[str] = []
        self.result_text: str | None = None
        self.created_at = datetime.now()

    async def run(self, deps: Deps) -> str:
        """Run via subprocess + push progress every 30s."""
        # ... wrap HermesClient.route() in asyncio.create_subprocess_shell
        # ... poll for new progress (нет прогресса — только start/end)
        # ... timeout 5 минут → graceful cancel
```

**Преимущества:**
- Минимум нового кода (≈250 строк)
- Использует уже работающий `HermesClient._route_cli()` (строки 94-109 client.py)
- Существующий fallback на LLM через `orchestrator.run()` уже работает
- Нулевой риск сломать текущий бот

**Недостатки:**
- `hermes peer list` → "No peer named 'rza'" (см. логи 10:04:44) — **сразу провалится в текущей Pavel's конфигурации**
- Нет real progress — `hermes peer dm` синхронный, блокирует 5 минут
- Hardcoded команда `hermes peer dm rza` — смена агенты = правка client.py
- peer-discovery runtime cost: каждый spawn холодный старт
- Subprocess overhead: 200-500ms на запуск, зависит от железа

**Трудозатраты:** 2-3 часа

**Подходит для:** проверки UX кнопки + happy path (когда Pavel зарегистрирует peer)

**Риск блокера:** **высокий** — прямо сейчас бот не сможет достучаться до Hermes. Но LLM-fallback через `orchestrator.run()` → `LLMClient.chat()` спасёт. Нужно явно показать юзеру что «Hermes недоступен, вот LLM-результат».

---

### Вариант B: In-process dispatcher с HermesClient wrapper + dynamic role prompts

**Что:** переиспользуем `HermesClient` (HTTP+CLI), но оборачиваем в `HermesDispatcher` с:
1. In-memory registry активных сессий (`dict[int, HermesSession]`)
2. Progress updates через `await bot.send_message(...)` каждые 30с
3. Таймаут 5 минут с graceful cancel
4. **Бонус**: возможность **inject SOUL.md как channel_prompt** (по Pavel's pattern) без subprocess

**Код (skeleton):**
```python
# app/hermes/dispatcher.py (новый)
class HermesDispatcher:
    def __init__(self, settings: Settings, storage: Storage, bot: Bot):
        self._client = HermesClient(settings)
        self._storage = storage
        self._bot = bot
        self._sessions: dict[int, HermesSession] = {}  # user_id → session
        self._tasks: dict[int, asyncio.Task] = {}

    async def spawn_session(
        self, *, chat_id: int, user_id: int, role: str,
        task: str, scenario: str, dept: Deps,
    ) -> int:
        """Create session row, spawn background task, return session_id."""
        session_id = await self._storage.create_hermes_session(
            user_id=user_id, chat_id=chat_id, role=role,
            task=task, scenario=scenario,
        )
        session = HermesSession(session_id=session_id, ...)
        self._sessions[user_id] = session
        task_obj = asyncio.create_task(self._run_session(session, dept))
        self._tasks[user_id] = task_obj
        return session_id

    async def _run_session(self, session, dept):
        """5-min timeout, progress messages every 30s, final result."""
        try:
            async with asyncio.timeout(300):
                result = await self._client.route(
                    role=session.role, task=session.task
                )
            # ... save to DB
            await self._storage.finish_hermes_session(
                session.session_id, status="done", result_text=result,
            )
            await dept.bot.send_message(chat_id=session.chat_id,
                                         text=result, ...)
        except asyncio.TimeoutError:
            await self._storage.finish_hermes_session(
                session.session_id, status="timeout",
            )
            await dept.bot.send_message(
                chat_id=session.chat_id,
                text="⏱ Hermes не ответил за 5 минут. Попробуй упростить задачу.",
            )


# app/max/handlers/hermes_button.py (новый)
async def on_hermes_menu(event: MessageCallback):
    await event.bot.send_callback(
        event.callback.callback_id,
        message=callback_message(
            HERMES_SUBMENU_TEXT,
            attachments=hermes_submenu_keyboard(),
        ),
    )

async def on_hermes_scenario(event: MessageCallback, scenario: str):
    """hermes:plan / hermes:research / hermes:custom → set state, wait text."""
    payload_map = {"plan": "hermes_plan_topic", "research": "hermes_research_topic",
                   "custom": "hermes_custom_task"}
    set_state(user_id, payload_map[scenario], {"scenario": scenario})
    await event.bot.send_callback(...)

# In menu.py:on_menu_text() — dispatch by action:
#   "hermes_plan_topic" → spawn_session(role='marketer', ...)
#   "hermes_research_topic" → spawn_session(role='researcher', ...)
#   "hermes_custom_task" → spawn_session(role='chat', ...)
```

**Преимущества:**
- **Fallback работает по дизайну** — `HermesClient` уже возвращает `None` при ошибке, dispatcher может сам дёрнуть `LLMClient.chat()` с правильным role
- Реальный progress через `await asyncio.sleep(30)` + `await bot.send_message/edit`
- In-memory registry → мгновенный доступ к активной сессии (для кнопки [Отменить] в будущем)
- Совместимо с таблицей `hermes_sessions` (уже создана)
- Можно добавить SOUL.md injection (Pavel's pattern) без переключения на gateway plugin

**Недостатки:**
- 6-8 часов (больше кода: timeout, progress loop, error handling)
- Progress loop идёт в `asyncio.create_task` — нужна осторожность с task cancellation
- Если Pavel's subprocess медленнее LLM — UX будет хуже LLM-fallback

**Трудозатраты:** 6-8 часов

**Подходит для:** production-ready кнопки, которая работает и с Hermes, и без

**Это РЕКОМЕНДУЕМЫЙ ВАРИАНТ.**

---

### Вариант C: Полная миграция на Pavel's plugin/max

**Что:** выкидываем наш FastAPI max-ai-bot, ставим Pavel's plugin в `~/.hermes/plugins/max/`, запускаем `hermes gateway run`. Все `[кнопки]` MAX → slash-команды → gateway plugin → RZA → LLM (miniMax-M3).

**Код (что нужно мигрировать):**
1. `app/max/handlers/*` → переписать как inline-custom logic в `adapter.py` (огромный файл)
2. `app/llm/prompts/*.py` → экспортировать как `~/.hermes/profiles/copywriter/SOUL.md`
3. `app/max/keyboards.py` → часть `main_menu_keyboard` пойдёт в `register()`, `/copy` уже занят Pavel's
4. `app/db/storage.py` → переписать на gateway SQLite (если поддерживается)
5. `app/max/ui.py` `ProgressReporter` → **выкинуть**, Pavel's использует `wants_progress_append` + `_keep_typing`
6. `app/hermes/client.py` → выкинуть, в Pavel's репо всё через `MessageEvent.channel_prompt`
7. `app/hermes/dispatcher.py` (наш) → **конфликт имён** с Pavel's `hermes dispatcher` (внутренний модуль)

**Преимущества:**
- Один deployment (`hermes gateway run`)
- Pavel's progress annotations богаче (он видит `💻 terminal`, `🔍 search`, etc.)
- Channel_prompt injection работает out of the box
- Peer model — можно послать задачу другому peer'у (RZA → GZA → Cappadonna)

**Недостатки:**
- **24-40 часов** (rewrite всего)
- **Ломает текущий flow** — потеряем `image_gen.py`, `post.py`, `callback_handler.py` в их текущем виде
- Конфликт имён: `dispatcher` (наш) vs `hermes dispatcher` (Pavel's core)
- Решение «плагин vs отдельный бот» — архитектурное, нужно одобрение Pavel's
- **`hermes peer list` всё равно "No peers registered"** — Pavel не настроил peer даже для себя
- 5 feature-флагов в `plugin.yaml` (`MAX_ALLOWED_USERS`, `MAX_HOME_CHANNEL`, etc.) — Pavel's way, не наш

**Трудозатраты:** 24-40 часов

**Подходит для:** полного перехода на Hermes Gateway (если Pavel решит, что max-ai-bot legacy)

**Не подходит для:** фазы C в REFACTOR-V3-PLAN (там явно «10-я кнопка + spawn», не миграция)

---

## 4. Сравнительная таблица

| Критерий | A (subprocess) | B (in-process dispatcher) | C (полная миграция) |
|---|---|---|---|
| **Трудозатраты** | 2-3 ч | 6-8 ч | 24-40 ч |
| **Блокирующий Pavel's peer** | ДА (сразу падает) | НЕТ (есть LLM fallback) | НЕТ |
| **Real progress updates** | НЕТ (синхронный CLI) | ДА (asyncio task loop) | ДА (Pavel's native) |
| **Использует существующий код** | 100% | 80% | 0% (rewrite) |
| **Совместимо с таблицей hermes_sessions** | ДА | ДА | НЕТ (Pavel's gateway SQLite) |
| **Изолирован от Pavel's `hermes dispatcher`** | ДА | ДА | НЕТ (конфликт) |
| **Сложность деплоя** | Просто | Просто | Hard (полная миграция) |
| **Риск регрессий** | Низкий | Низкий | Высокий |
| **SOUL.md injection (channel_prompt)** | НЕТ | ДА (легко добавить) | ДА (native) |
| **Masta Killa одобрит** | Да, но обидно | Да ✅ | Нет (out of scope) |
| **Долгосрочная стратегия** | Legacy | **Правильный путь** | Параллельная вселенная |

---

## 5. Архитектурные trade-offs

### Надёжность (если Hermes не работает)

| | Поведение |
|---|---|
| **A** | Subprocess падает → `HermesClient._route_cli()` returns `None` → `Orchestrator.run()` → LLM fallback. Юзер видит Hermes-результат как обычный LLM (но без Hermes-бейджа). **Проблема:** в UI нельзя отличить «Hermes» от «обычный LLM». |
| **B** | `HermesDispatcher.spawn_session()` ловит `None` → переключает session.status на `done` и ставит fallback marker → юзер видит «⚠️ Hermes недоступен, вот LLM-результат (запасной вариант)» |
| **C** | Pavel's plugin наследует `BasePlatformAdapter` — нет нашего fallback. Если RZA не зарегистрирован, `peer dm rza` не работает → 5-минутный timeout → silent fail |

**Вердикт:** B — единственный вариант с **честным** UX. A прячет проблему, C имеет silent failure.

### Производительность

| | Cold start | Hot path |
|---|---|---|
| **A** | 200-500ms subprocess + binary search в PATH | CLI subprocess 1 раз на сессию |
| **B** | 10-50ms (asyncio.create_task) | In-process async call |
| **C** | 1000ms (gateway startup) + всё то же | Параллельный runner |

**Вердикт:** B — fastest hot path. A — slowest из-за subprocess.

### Совместимость с текущим кодом

| | Touched files |
|---|---|
| **A** | `app/max/handlers/hermes_button.py` (NEW, ~80 строк), `app/max/client.py` (add 1 line), `app/hermes/session.py` (NEW, ~250 строк) |
| **B** | + `app/hermes/dispatcher.py` (NEW, ~300 строк), `app/max/handlers/menu.py` (extend `on_menu_text` для `hermes_*` actions) |
| **C** | ~12 файлов: deprecate `app/hermes/`, `app/core/`, `app/llm/prompts/*`, partial `app/max/*` |

**Вердикт:** A — well-scoped. B — moderately scoped. C — reckless.

### Совместимость с Pavel's roadmap

| | Что может появиться в Pavel's репо |
|---|---|
| **A** | Когда Pavel зарегистрирует peer `rza`, наш subprocess заработает из коробки. 0 изменений. |
| **B** | Когда Pavel зарегистрирует peer, можно переключить `HermesDispatcher._run_session` на использование нового HTTP endpoint (если Pavel его поднимет). Лёгкая эволюция. |
| **C** | Полностью подчиняемся Pavel's release cycle. Наш deployment timeline = Pavel's timeline. |

**Вердикт:** A→B эволюция; B→C миграция, не смешиваются.

---

## 6. Рекомендация: Вариант B

**Почему:**

1. **Hermes прямо сейчас недоступен** (peer не зарегистрирован, HTTP endpoint молчит). Вариант A сразу провалится — даже если Юзер нажмёт [🤖 Hermes], он получит копию LLM-fallback без понимания что произошло. Вариант B **честно** покажет «Hermes недоступен, но вот LLM-вариант».

2. **Backend совместимости.** `HermesClient` остаётся. `HermesDispatcher` — обёртка для UX (progress, timeout, fallback-инжект). Когда Pavel зарегистрирует peer, dispatcher автоматически заработает без изменений.

3. **Задел на SOUL.md injection** (Pavel's killer feature). `HermesDispatcher._run_session()` может читать `app/hermes/souls/{role}.md` (если положить) и инжектить как prefix в `task`, имитируя Pavel's `channel_prompt` без subprocess.

4. **6-8 часов** — это полный рабочий день. Покрывается 1 проходом Masta Killa (sub-agent), как и запланировано в REFACTOR-V3-PLAN.

5. **Локализация UX.** Возможность добавить rule `if self._settings.hermes_mode == "none" and self._settings.llm_api_key == "": прячем кнопку` — то есть **кнопка пропадёт**, если бот вообще не сможет ничего ответить. Чисто, по делу.

6. **Нулевая миграция.** REFACTOR-V3-PLAN §фаза C явно требует «10-я кнопка + spawn + storage + 3 сценария» — это и есть вариант B. Фаза D (тесты) — естественное продолжение.

---

## 7. Конкретный план реализации (Вариант B)

### 7.1 Новые файлы

**`app/hermes/session.py`** (~150 строк)
```python
@dataclass
class HermesSession:
    session_id: int
    chat_id: int
    user_id: int
    role: str
    task: str
    scenario: str
    status: str = "running"  # running | done | failed | timeout | fallback
    progress: list[str] = field(default_factory=list)
    result_text: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None

    def add_progress(self, line: str) -> None: ...
    def is_active(self) -> bool: ...
```

**`app/hermes/dispatcher.py`** (~300 строк)
```python
class HermesDispatcher:
    def __init__(self, settings, storage, bot, client):
        self._settings = settings
        self._storage = storage
        self._bot = bot
        self._client = client  # HermesClient
        self._active: dict[int, HermesSession] = {}  # user_id → session
        self._tasks: dict[int, asyncio.Task] = {}

    async def spawn_session(self, *, chat_id, user_id, role, task, scenario) -> int:
        session_id = await self._storage.create_hermes_session(...)
        session = HermesSession(session_id=session_id, ...)
        self._active[user_id] = session
        self._tasks[user_id] = asyncio.create_task(self._run_session(session))
        return session_id

    async def _run_session(self, session: HermesSession) -> None:
        progress_msg_id = None
        try:
            async with asyncio.timeout(300):  # 5 minutes
                # 1. Send initial progress message
                progress_msg_id = await self._send_initial_progress(session)

                # 2. Try HermesClient (HTTP→CLI fallback chain)
                answer = await self._client.route(role=session.role, task=session.task)

                if answer is not None:
                    # Hermes worked
                    session.result_text = answer
                    session.status = "done"
                    await self._storage.finish_hermes_session(...)
                    await self._send_final(session, answer, source="hermes")
                else:
                    # Fallback to LLM
                    session.status = "fallback"
                    llm_answer = await self._llm.chat(...)
                    session.result_text = llm_answer
                    await self._storage.finish_hermes_session(...)
                    await self._send_final(session, llm_answer, source="llm-fallback")
        except asyncio.TimeoutError:
            session.status = "timeout"
            await self._storage.finish_hermes_session(...)
            await self._send_timeout_message(session)
        except Exception as e:
            session.status = "failed"
            await self._storage.finish_hermes_session(...)
            await self._send_error_message(session, e)
        finally:
            self._active.pop(session.user_id, None)
            self._tasks.pop(session.user_id, None)

    async def _send_initial_progress(self, session) -> str:
        """Send '🤖 Hermes запускает <role>...' message, return mid."""
        # ... use MarkdownSender or ProgressReporter

    async def _send_final(self, session, text, *, source) -> None:
        """Send final result with [🏠 В меню] reply."""
        # If source == "llm-fallback" — prepend warning
        # Else (hermes) — show happy path

    async def _send_timeout_message(self, session) -> None: ...
    async def _send_error_message(self, session, e) -> None: ...
```

**`app/max/handlers/hermes_button.py`** (~120 строк)
```python
HERMES_MODE_DESCRIPTIONS = {
    "plan": "📊 **Контент-план**\n\nЗапущу Hermes-агента (роль: Маркетолог) для контент-плана.\n\nВведи нишу. Например: 14 дней | кофейня",
    "research": "📝 **Исследование**\n\nЗапущу Hermes-агента (роль: Исследователь) для глубокого брифа.\n\nВведи тему. Например: влияние ИИ на копирайтинг в 2026",
    "custom": "🎯 **Своя задача**\n\nЗапущу Hermes-агента (роль: Чат) для свободной задачи.\n\nВведи задачу. Например: найди 5 идей названий для подкаста",
}

HERMES_FALLBACK_BANNER = "⚠️ Hermes сейчас недоступен. Покажу LLM-результат (fallback)."

SCENARIO_TO_ROLE = {
    "plan": "marketer",
    "research": "researcher",
    "custom": "chat",
}

def register(dp: Dispatcher, deps: Deps) -> None:
    @dp.message_callback()
    async def on_hermes_callback(event: MessageCallback) -> None:
        payload = (event.callback.payload or "").strip()
        if not payload.startswith("hermes"):
            return

        if payload == "hermes":
            # show submenu
            await _send_hermes_submenu(event)
            return

        if payload in ("hermes:plan", "hermes:research", "hermes:custom"):
            scenario = payload.split(":", 1)[1]
            _set_hermes_state(deps, event, scenario)
            return

    @dp.message_created()
    async def on_hermes_input(event: MessageCreated) -> None:
        # ... handled in menu.py:on_menu_text() — see 7.2
```

### 7.2 Изменения в существующих файлах

**`app/max/handlers/menu.py`** (строки 215-228 — добавить после `elif action == "post"`):
```python
# Hermes scenarios (Feature V3)
elif action == "hermes_plan_topic":
    scenario = "plan"
elif action == "hermes_research_topic":
    scenario = "research"
elif action == "hermes_custom_task":
    scenario = "custom"

if scenario:
    # spawn background task
    await _spawn_hermes(deps, event, scenario, text)
    return
```

И **отдельный helper** `_spawn_hermes(deps, event, scenario, task)`:
```python
async def _spawn_hermes(deps: Deps, event: MessageCreated, scenario: str, task: str) -> None:
    chat_id, user_id = event.get_ids()
    role = SCENARIO_TO_ROLE[scenario]
    # Inline progress message
    async with ProgressReporter(event, "🤖 Hermes запускает задачу…") as prog:
        await deps.dispatcher.spawn_session(
            chat_id=chat_id, user_id=user_id, role=role,
            task=task, scenario=scenario,
        )
        # ... не дожидаемся! spawn_session возвращает id immediately
        await prog.step("✅ Задача поставлена в очередь. Жди результат в чате.")
        await prog.flush()
```

**`app/max/handlers/deps.py`** — добавить в `Deps`:
```python
@dataclass
class Deps:
    bot: Bot
    dp: Dispatcher
    orchestrator: Orchestrator
    storage: Storage
    publisher: Publisher
    auth: AuthGate
    dispatcher: HermesDispatcher  # NEW
```

**`app/context.py`** (строки 41-55) — создать dispatcher:
```python
async def init_context(settings: Optional[Settings] = None) -> AppContext:
    settings = settings or get_settings()
    storage = Storage(settings.db_path)
    await storage.init()
    llm = LLMClient(settings)
    hermes_client = HermesClient(settings)  # NEW
    orchestrator = Orchestrator(settings, llm, storage)
    # Dispatcher сначала без bot (bot ещё не создан на этой фазе)
    dispatcher = HermesDispatcher(settings, storage, bot=None, client=hermes_client)
    # ... 
```

**`app/max/client.py`** (строка 91) — добавить регистрацию:
```python
from app.max.handlers import hermes_button  # NEW

def register_handlers(dp: Dispatcher, deps: Deps) -> None:
    # ...
    hermes_button.register(dp, deps)  # NEW
```

**`app/max/keyboards.py`** — **уже готово** (кнопка [🤖 Hermes] в строке 38, `hermes_submenu_keyboard()` в строке 44).

**`app/max/descriptions.py`** — **добавить** `hermes` description:
```python
"hermes": (
    "🤖 **Hermes**\n\n"
    "Запускает Hermes-агента в фоне. Прогресс придёт в чат.\n\n"
    "**3 сценария:**\n"
    "📊 **Контент-план** — для ниши на N дней\n"
    "📝 **Исследование** — глубокий бриф с источниками\n"
    "🎯 **Своя задача** — что угодно\n\n"
    "Если Hermes недоступен, бот покажет LLM-fallback."
),
```

### 7.3 Тесты (≥8 новых)

В `tests/`:
- `test_hermes_session.py` — `HermesSession.is_active()`, lifecycle states
- `test_hermes_dispatcher.py` — mock `HermesClient` + verify `spawn_session` создаёт DB row, запускает task, шлёт progress
- `test_hermes_timeout.py` — mock медленный `HermesClient`, verify 5-min timeout + status='timeout'
- `test_hermes_fallback.py` — mock `HermesClient.route()` returns `None`, verify dispatch на LLM + status='fallback'
- `test_hermes_submenu.py` — callback `hermes` → `hermes_submenu_keyboard()`, `hermes:plan` → set state
- `test_hermes_spawn.py` — end-to-end: callback → text input → `spawn_session` mock called с правильным role
- `test_hermes_button_register.py` — verify `register(dp, deps)` mounts callbacks
- `test_hermes_descriptions.py` — `COMMAND_DESCRIPTIONS['hermes']` содержит все 3 сценария

**Цель:** 95+ passed (82 → 95+).

### 7.4 Деплой

1. PowerShell kill порт 8080 (как в REFACTOR-V3-PLAN §Деплой)
2. `uvicorn app.main:app --host 0.0.0.0 --port 8080` в фоне
3. Проверить «Application startup complete» в логе
4. **Новый визуальный тест:** нажать [🤖 Hermes] в MAX → должно появиться подменю с 3 кнопками → нажать [📊 Контент-план] → ввести тему → прогресс-сообщение → финальный результат

### 7.5 Acceptance (фаза C)

| # | Критерий | Цель |
|---|---|---|
| 1 | `app/hermes/session.py` существует | ≥150 строк, dataclass |
| 2 | `app/hermes/dispatcher.py` существует | ≥250 строк, 5-min timeout |
| 3 | `app/max/handlers/hermes_button.py` существует | ≥120 строк, 3 сценария |
| 4 | Регистрация в `app/max/client.py` | 1 line added |
| 5 | `hermes:plan` payload → set_state + spawn | Ручной клик в MAX |
| 6 | Fallback работает | Mock `HermesClient.route()` returns None → LLM |
| 7 | Тесты | 95+ passed |
| 8 | HERMESMODE disabled + no LLM key → кнопка скрыта | Проверить `keyboards.py` пропускает [🤖 Hermes] |

---

## 8. Что брать из Pavel's репо (конкретно)

| Файл/паттерн Pavel's | Наш файл/паттерн | Зачем |
|---|---|---|
| `_load_role_registry()` (`adapter.py:281-302`) | `app/hermes/souls/registry.yaml` (если решим делать SOUL.md) | Динамическое подключение новых ролей через YAML |
| `_get_soul_content()` (`adapter.py:305-318`) | `app/hermes/souls/loader.py` | Читать SOUL.md без try/except в бизнес-коде |
| `_ROLE_COMMANDS` dict (`adapter.py:278`) | Data-driven `SCENARIO_TO_ROLE` в `hermes_button.py` | Если решим расширять сценарии data-only |
| `_ROLE_REGISTRY_PATH = Path(__file__).parent / "role_registry.yaml"` | `app/hermes/souls/role_registry.yaml` | Путь внутри проекта, не `~/.hermes` |
| role_registry.yaml структура | Наша копия с 3 сценариями | Готовый формат |
| `_HELP_RU`/`_MENU_RU` (`adapter.py:182-247`) | `HERMES_MODE_DESCRIPTIONS` в `hermes_button.py` | Стиль коротких русских подсказок |
| `_ROLE_REGISTRY_PATH` env override | `HERMES_SOUL_DIR` env var | Future expansion |

**Прямо сейчас** в фазе C — нужно только `_get_soul_content()` + `_HELP_RU` style. Остальное — на фазу D или позже.

---

## 9. Открытые вопросы для Pavel

1. **Когда Pavel зарегистрирует peer `rza`?** — если на этой неделе, можно сразу в A; если через месяц, B и спокойно.
2. **Планируется ли HTTP endpoint на стороне Hermes?** — `HERMES_RZA_URL` сейчас мёртв. Если Pavel поднимет `/api/hermes/route`, dispatcher сможет ловить streaming progress (SSE — это P2 feature).
3. **Будет ли Pavel's репо merge в основной `hermes-agent`?** — если да, вариант C становится стратегическим через 3-6 месяцев. Если нет, B остаётся надолго.
4. **Согласен ли Pavel, чтобы мы скопировали структуру role_registry.yaml в `app/hermes/souls/`?** — формат идентичный, делаем upstream-friendly.

---

## 10. Резюме

**Что делаем:** вариант B — in-process dispatcher + новый handler.

**Что НЕ делаем:**
- ❌ Не ставим Pavel's plugin в `~/.hermes/plugins/max` (out of scope)
- ❌ Не переписываем max-ai-bot под Hermes Gateway (фаза C = кнопка, не миграция)
- ❌ Не убираем `HermesClient` — оставляем как backend, dispatcher как UX

**Что уже есть:**
- ✅ Кнопка [🤖 Hermes] в `keyboards.py:38`
- ✅ `hermes_submenu_keyboard()` с 3 сценариями в `keyboards.py:44-53`
- ✅ Таблица `hermes_sessions` + CRUD в `db/storage.py:112-294`
- ✅ `HermesClient` с auto fallback HTTP→CLI в `hermes/client.py`
- ✅ `Orchestrator.run()` с LLM fallback в `core/orchestrator.py:131-151`

**Что добавляем:**
- `app/hermes/session.py` — dataclass (150 строк)
- `app/hermes/dispatcher.py` — async dispatcher (300 строк)
- `app/max/handlers/hermes_button.py` — button handler (120 строк)
- 3-4 строки в `app/max/handlers/menu.py` — dispatch новых actions
- 2-3 строки в `app/max/client.py` — register handler
- 1 строка в `app/max/handlers/deps.py` — `dispatcher` field
- 3-4 строки в `app/context.py` — wire dispatcher
- 1 описание в `app/max/descriptions.py` — `hermes` text
- 8+ новых теста

**Когда:** 1 проход Masta Killa, 6-8 часов.
**Блокирующий риск:** нет — LLM fallback спасает.
**Pavel's roadmap:** совместимо (вариант A готов к переключению, когда peer зарегистрируют).
