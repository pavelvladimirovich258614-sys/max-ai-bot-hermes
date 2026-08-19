# Enhancement Design — MAX AI Bot (GZA draft)

> Цель документа: спека усиления бота. Стек: Python 3.12, SDK `maxapi`,
> FastAPI+uvicorn, httpx, pydantic-settings, aiosqlite. Без внешних web-исследований —
> опираемся на уже известный код бота (`app/max/*`, `app/db/storage.py`,
> `app/core/orchestrator.py`, `app/llm/*`, `app/config.py`) и на Hermes skills.

Ключевые факты из кодовой базы (используются в псевдокоде ниже):
- `maxapi`: `Bot`, `Dispatcher`, `dp.message_created()`, `dp.message_callback()`,
  фильтр `F`, типы `MessageCreated`, `MessageCallback`, `CallbackButton`,
  `InlineKeyboardBuilder`, `MessageForCallback`.
- Обёртка `CompliantBot` (`app/max/bot_wrapper.py`): API_URL =
  `https://platform-api2.max.ru`, токен шлётся как `Authorization: <token>`.
- Ответ в том же чате: `await event.message.answer(text, attachments=...)`.
- Отправка в канал: `await bot.send_message(chat_id=<id>, text=...)`.
- `chat_id, user_id = event.get_ids()`.
- Callback: `event.callback.callback_id`, `event.bot.send_callback(callback_id, message=MessageForCallback(...))`.
- Подписки бота на каналы: `await bot.get_subscriptions()` → `.subscriptions[]` с
  `.chat_id`, `.name`/`.title`.
- Админы: `settings.admin_user_ids` (list[int], из `MAX_ADMIN_USER_IDS`).
- Storage (`app/db/storage.py`): таблицы `users`, `messages`, `publications`,
  `sessions`; методы `add_message`, `recent_messages`, `get_session_context`,
  `upsert_user`, `get_user`, `create_publication`, `get_publication`,
  `update_publication`.
- Orchestrator (`app/core/orchestrator.py`): `_system_prompt(role)` грузит
  `SYSTEM_PROMPT` из `app/llm/prompts/<role>.py`. Роль → Hermes (RZA) с фолбэком
  на прямой LLM.
- LLM (`app/llm/client.py`): `LLMClient.chat(messages, role=, system=, max_tokens=)`.
  Primary = MiniMax-M3 через **anthropic-style** `POST {base_url}/v1/messages`
  (`base_url=https://api.minimax.io/anthropic`, `llm_primary_style="anthropic"`).
  Fallback = StepFun `step-3.7-flash` **openai-style**
  (`https://api.stepfun.ai/v1`, `llm_fallback_style="openai"`).

---

## 1. Skills map

Как вшить: в `app/core/orchestrator.py` добавить центральный маппинг
`ROLE_SKILLS` и дописывать секцию `## Доступные навыки (skills)` в
`_system_prompt(role)` (для LLM-фолбэка). Для Hermes-маршрутизации эти же имена
передаются в `context` и используются RZA-специалистом.

```python
# app/core/orchestrator.py
ROLE_SKILLS: dict[str, list[str]] = {
    "researcher":       ["grounded-citations", "competitor-news-monitor",
                          "blogwatcher", "arxiv", "llm-wiki", "blocked-page-recovery"],
    "copywriter":       ["humanizer", "baoyu-infographic", "document-to-action-items",
                          "meeting-action-items", "popular-web-designs", "claude-design"],
    "copywriter_extra": [],   # humanizer ОБЯЗАТЕЛЬНО, остальные — максимум 5-6
    "prompt_engineer":  ["plan", "test-driven-development", "systematic-debugging",
                          "requesting-code-review", "hermes-agent-skill-authoring"],
    "analyzer":         ["ocr-and-documents", "pdf", "youtube-content",
                          "document-to-action-items", "nano-pdf"],
}

def _system_prompt(role: str) -> str:
    base = _load_base(role)                       # текущий getattr(mod, "SYSTEM_PROMPT")
    skills = ROLE_SKILLS.get(role)
    if skills:
        block = "\n\n## Доступные навыки (skills)\n" + "\n".join(
            f"- {s}" for s in skills
        )
        base += block
    return base
```

| Роль бота | Skill (имя) | Что даёт роли | Как вшить в SYSTEM_PROMPT (конкретная фраза/структура) |
|---|---|---|---|
| researcher | `grounded-citations` | Заставляет цитировать проверяемые источники (URL), не выдумывать факты | «- grounded-citations: всегда цитируй проверяемые источники (URL), не выдумывай.» |
| researcher | `competitor-news-monitor` | Мониторинг новостей конкурента с цитатами | «- competitor-news-monitor: при запросе мониторинга конкурента — давай свежие newости с цитатами.» |
| researcher | `blogwatcher` | Подписки на RSS/блоги для свежего контента | «- blogwatcher: используй подписки на блоги/RSS для актуального контента.» |
| researcher | `arxiv` | Поиск научных/техстатей | «- arxiv: для научных/тех тем ищи статьи на arXiv.» |
| researcher | `llm-wiki` | Сверка междисциплинарных фактов | «- llm-wiki: для фактов сверяйся с LLM Wiki.» |
| researcher | `blocked-page-recovery` | Обход платных/WAF-страниц через фолбэки | «- blocked-page-recovery: если страница заблокирована — пробуй fallback-источники.» |
| copywriter | `humanizer` | Убирает AI-измы, живой язык | «- humanizer: убирай AI-измы, пиши живым человеческим языком.» |
| copywriter | `baoyu-infographic` | Инфографика 21×21 лейаутов | «- baoyu-infographic: если нужна инфографика — структурируй данные под 21×21.» |
| copywriter | `document-to-action-items` | Документ → цитируемые задачи/дедлайны | «- document-to-action-items: превращай документ в цитируемые задачи/дедлайны.» |
| copywriter | `meeting-action-items` | Протокол → решения, ответственные, тикеты | «- meeting-action-items: из протокола встречи — решения, ответственные, тикеты.» |
| copywriter | `popular-web-designs` | Ориентир на дизайн-системы Stripe/Linear/Vercel | «- popular-web-designs: ориентируйся на дизайн-системы Stripe/Linear/Vercel.» |
| copywriter | `claude-design` (опц.) | One-off HTML-артефакты (лендинги/прототипы) | «- claude-design: для одностраничных HTML-макетов используй этот навык.» |
| prompt_engineer | `plan` | План реализации промпта до кода | «- plan: составь план промпта до написания кода.» |
| prompt_engineer | `test-driven-development` | Тесты/кейсы промпта до финала | «- test-driven-development: пиши кейсы/тесты промпта до финала.» |
| prompt_engineer | `systematic-debugging` | Поиск корня проблемы промпта | «- systematic-debugging: ищи корень проблемы промпта, не чини наугад.» |
| prompt_engineer | `requesting-code-review` | Саморевью промпта перед финалом | «- requesting-code-review: перед финалом — саморевью промпта.» |
| prompt_engineer | `hermes-agent-skill-authoring` (опц.) | Авторинг SKILL.md | «- hermes-agent-skill-authoring: при создании навыка используй этот формат.» |
| analyzer | `ocr-and-documents` | Извлечение текста из сканов/PDF | «- ocr-and-documents: извлекай текст из сканов/PDF перед анализом.» |
| analyzer | `pdf` | Работа с PDF (извлечение/слияние/заполнение) | «- pdf: работай с PDF (извлечение, слияние, заполнение).» |
| analyzer | `youtube-content` | Транскрипт+резюме YouTube по URL | «- youtube-content: транскрибируй и резюмируй YouTube по URL.» |
| analyzer | `document-to-action-items` | Документ → цитируемые обязательства | «- document-to-action-items: из документа — цитируемые обязательства и задачи.» |
| analyzer | `nano-pdf` (опц.) | Прямое редактирование текста в PDF | «- nano-pdf: для правки текста в существующем PDF.» |

> Примечание по `copywriter`: обязательны `humanizer`, `baoyu-infographic`,
> `document-to-action-items`, `meeting-action-items`, `popular-web-designs`
> (ровно 5, как в ТЗ). `claude-design` — опциональный 6-й.

---

## 2. Chats & channels listening

### Как maxapi доставляет события из групп/каналов
Все обновления приходят как `MessageCreated`. Тип чата определяется через
`event.message.chat`:

```python
chat = event.message.chat
chat_type = getattr(chat, "type", "dialog")   # "dialog" | "chat" | "channel"
sender = event.message.sender                 # объект отправителя (user/bot)
text = (event.message.body.text or "").strip()
```

- **Личка (dialog)**: `chat.type == "dialog"` (нет title, sender — пользователь).
- **Группа (chat)**: `chat.type == "chat"`.
- **Канал (channel)**: `chat.type == "channel"` либо сообщение приходит как
  channel-post (bot подписан на канал).

### Права бота
- **Группы**: добавить бота в группу **администратором** (нужно для антиспама —
  удаление сообщений и бан через maxapi). Без прав админа антиспам сможет только
  логировать/мьютить в БД.
- **Каналы**: подписать бота на канал (`bot.get_subscriptions()` вернёт его) и
  дать право публиковать. Отправка поста: `bot.send_message(chat_id=<channel_id>, text=...)`.

### Как отвечать
- В том же чате: `await event.message.answer(...)`.
- В канал (односторонняя публикация): `await bot.send_message(chat_id=..., text=...)`.
- Reply на конкретное сообщение (если API поддерживает): `event.message.answer(...,
  reply_to=msg_id)` — проверить в maxapi; fallback — просто `answer`.

### Точка интеграции
Новый хендлер `app/max/handlers/group_listen.py`, регистрация в
`app/max/client.py::register_handlers`:

```python
# app/max/client.py
from app.max.handlers import group_listen
...
group_listen.register(dp, deps)
```

### Псевдокод обработчика
```python
# app/max/handlers/group_listen.py
from maxapi import Dispatcher
from maxapi.types import MessageCreated
from app.max.handlers.deps import Deps
from app.config import get_settings

async def _is_admin_mention_or_cmd(text: str, settings) -> bool:
    return text.startswith("/") or (settings.max_bot_username and settings.max_bot_username in text)

def register(dp: Dispatcher, deps: Deps) -> None:
    s = get_settings()

    @dp.message_created()
    async def on_group_message(event: MessageCreated) -> None:
        chat = event.message.chat
        chat_type = getattr(chat, "type", "dialog")
        text = (event.message.body.text or "").strip()
        chat_id, user_id = event.get_ids()

        # 1) Личку игнорим, если выключен private-режим
        if chat_type == "dialog" and not s.allow_private_chat:
            return

        # 2) В группах/каналах реагируем только на команды или упоминания бота
        if chat_type in ("chat", "channel") and not await _is_admin_mention_or_cmd(text, s):
            return

        # 3) Пропускаем админов (см. антиспам)
        if user_id in s.admin_user_ids:
            pass  # админы не под антиспамом

        # 4) Роутинг: команда -> роль/экшен
        if text.startswith("/research"):
            await do_research(deps, event, text[len("/research"):].strip())
        elif text.startswith("/analyze"):
            await do_analyze(deps, event, text[len("/analyze"):].strip())
        # ... остальные команды
        else:
            # свободный диалог в группе (только при упоминании)
            await deps.orchestrator.run(role="chat", task=text,
                                        context={"source": "max", "chat_type": chat_type},
                                        chat_id=chat_id, user_id=user_id)
```

> Важно: `free_chat` и `menu.on_menu_text` уже вешают `@dp.message_created()`.
> maxapi вызывает все подходящие хендлеры. Чтобы group_listen не дублировал логику
> menu/free_chat в личке, ставим фильтр по `chat_type` и по команде/упоминанию
> (как выше). Приоритет антиспама — см. раздел 3 (middleware ДО основных хендлеров).

---

## 3. Antispam system

### Дизайн
- **Rate-limit по `user_id`**: окно `N` сек + `M` сообщений (например, `N=10`,
  `M=5`). Храним таймстемпы в БД, считаем за окно.
- **Детект дублей**: хеш нормализованного текста (`md5`), сравнение с последними
  `K` сообщений пользователя/чата. Одинаковый хеш → спам.
- **Бан-слова**: чёрный список в `.env` `ANTISPAM_BAN_WORDS` (через запятую).
- **Админ-лист**: `settings.admin_user_ids` — админы всегда пропускаются.
- **Тихий режим (mute)**: мьют на `N` минут. Если maxapi поддерживает
  `restrict_chat_member`/`chat_ban` — применяем; иначе заносим в `antispam_bans`
  и тихо дропаем сообщения этого юзера до истечения mute.
- **Авто-действия через maxapi**: `delete_message(msg_id)` (удалить сообщение),
  `ban_chat_member`/`chat_ban` (если доступно в SDK) — иначе mute через БД.

### Где хранить (новые таблицы в `app/db/storage.py`)
```sql
CREATE TABLE IF NOT EXISTS antispam_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    user_id INTEGER,
    text_hash TEXT,
    raw_text TEXT,
    msg_id TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS antispam_bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    user_id INTEGER,
    banned_until TEXT,      -- ISO; NULL = перманент
    reason TEXT,
    created_at TEXT
);
```

Новые методы Storage:
```python
async def add_antispam_message(self, chat_id, user_id, text_hash, raw_text, msg_id):
    ...
async def recent_antispam(self, chat_id, user_id, limit=K) -> list[Row]:
    """Последние K сообщений юзера/чата (для детекта дублей)."""
    ...
async def count_in_window(self, chat_id, user_id, window_sec: int) -> int:
    """Сколько сообщений за последние window_sec."""
    ...
async def is_banned(self, chat_id, user_id) -> bool:
    """Есть ли активный бан/mute."""
    ...
async def add_ban(self, chat_id, user_id, banned_until, reason):
    ...
```

### Точка интеграции
Middleware ДО основных хендлеров. В `app/max/client.py::setup_bot` после
`register_handlers` добавить:

```python
from app.max.antispam import AntispamMiddleware
dp.update_middleware(AntispamMiddleware(deps.storage, get_settings()))
```

(Если maxapi не экспонирует `update_middleware`, зарегистрировать
`@dp.message_created()` хендлер с высоким приоритетом в начале `register_handlers`
и при блоке — `return`, чтобы дальше не шло. Регистрируем antispam-хендлер первым.)

### Псевдокод проверки
```python
# app/max/antispam.py
import hashlib, time
from app.config import Settings
from app.db.storage import Storage

BAN_WORDS = [w.strip().lower() for w in (Settings().antispam_ban_words or "").split(",") if w.strip()]

def _norm(t: str) -> str:
    return " ".join(t.lower().split())

def _hash(t: str) -> str:
    return hashlib.md5(_norm(t).encode()).hexdigest()

class AntispamMiddleware:
    def __init__(self, storage: Storage, settings: Settings):
        self.storage = storage
        self.s = settings
        self.WINDOW, self.MAX_MSG = 10, 5
        self.DUP_LOOKBACK = 10

    async def __call__(self, event, *args, **kwargs):
        chat_id, user_id = event.get_ids()
        text = (event.message.body.text or "")

        # админы — всегда ок
        if user_id in self.s.admin_user_ids:
            return await self._next(event, *args, **kwargs)

        # активный бан/mute
        if await self.storage.is_banned(chat_id, user_id):
            await self._delete(event)
            return  # блокируем дальше

        # бан-слова
        if any(w and w in text.lower() for w in BAN_WORDS):
            await self._ban(event, reason="ban-word")
            return

        # дубли
        h = _hash(text)
        recent = await self.storage.recent_antispam(chat_id, user_id, limit=self.DUP_LOOKBACK)
        if h in {r["text_hash"] for r in recent}:
            await self._mute_or_delete(event, reason="duplicate")
            return

        # rate-limit
        if await self.storage.count_in_window(chat_id, user_id, self.WINDOW) >= self.MAX_MSG:
            await self._mute_or_delete(event, reason="flood")
            return

        # чисто — логируем и пропускаем
        await self.storage.add_antispam_message(chat_id, user_id, h, text,
                                                 getattr(event.message, "id", None))
        return await self._next(event, *args, **kwargs)

    async def _delete(self, event):
        msg_id = getattr(event.message, "id", None)
        if msg_id:
            try: await event.bot.delete_message(msg_id)
            except Exception: pass

    async def _mute_or_delete(self, event, reason):
        await self._delete(event)
        # mute на 10 мин (или permanent бан при повторе — логика на усмотрение)
        until = time.time() + 600
        await self.storage.add_ban(event.get_ids()[1] and None, event.get_ids()[0],
                                   event.get_ids()[1], until, reason)
        # при наличии chat_ban: await event.bot.ban_chat_member(chat_id, user_id)

    async def _ban(self, event, reason):
        await self._delete(event)
        await self.storage.add_ban(...)   # permanent
```

> Конфиг: добавить в `app/config.py` поля `antispam_ban_words: str = ""` и
> `antispam_enabled: bool = True`, и в `.env` — `ANTISPAM_BAN_WORDS=...,...,...`.

---

## 4. Image analysis

### Как maxapi приносит attachments
В `MessageCreated` вложения лежат в `event.message.body.attachments` (либо
`event.message.attachments`) — список объектов `Attachment` с полями `url`, `type`:

```python
def _image_urls(event) -> list[str]:
    body = event.message.body
    atts = getattr(body, "attachments", None) or getattr(event.message, "attachments", None) or []
    out = []
    for a in atts:
        url = getattr(a, "url", None)
        atype = getattr(a, "type", "") or ""
        if url and ("image" in atype or atype in ("photo", "picture")):
            out.append(url)
    return out
```

### Как скачать (httpx)
```python
import httpx, base64

async def download_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content

def to_b64(data: bytes, media_type: str = "image/jpeg") -> str:
    return base64.b64encode(data).decode()
```

### Умеют ли LLM видеть картинки (статус, без долгого поиска)
- **MiniMax-M3 (primary)** — подключён через **anthropic-style** эндпоинт
  (`base_url=https://api.minimax.io/anthropic`, `llm_primary_style="anthropic"`).
  Формат Anthropic Messages API **нативно поддерживает image content blocks**
  (`{"type":"image","source":{"type":"base64","media_type":...,"data":...}}`).
  ⇒ MiniMax-M3 **вероятно поддерживает vision** через этот эндпоинт, но это надо
  подтвердить коротким тестовым вызовом (см. ниже). **Статус: ВЕРОЯТНО ДА, нужен
  тест.**
- **StepFun `step-3.7-flash` (fallback)** — openai-style
  (`https://api.stepfun.ai/v1`, `llm_fallback_style="openai"`). OpenAI
  chat/completions поддерживает `image_url` content blocks **только если модель
  мультимодальна**. Неизвестно, является ли `step-3.7-flash` vision-моделью.
  ⇒ **Статус: НЕИЗВЕСТНО — проверить через `GET {base_url}/v1/models`**
  (посмотреть, есть ли у модели multimodal/vision capability).

> Быстрая проверка MiniMax-M3 vision (тест, не в проде):
> ```python
> await deps.orchestrator.run_vision(role="analyzer",
>     task="Что изображено?", images=[b64])
> ```
> Если приходит осмысленное описание — vision работает.

### Fallback если LLM не видит картинку
1. **OCR** через навык `ocr-and-documents` (извлечь текст со скриншота/документа),
   затем отдать текст LLM как обычный анализ.
2. Прямой вызов отдельной vision-модели, если есть ключ (например, отдельный
   `vision` provider в `app/config.py` + ветка в `LLMClient`).

### Куда в меню
- В `app/max/keyboards.py::main_menu_keyboard()` добавить кнопку
  `🖼 Анализ картинки` с `payload="image"` (фактически сейчас в клавиатуре 8
  кнопок: Research, Copy, Plan, Ideate, Analyze, Prompt, Post, Help — добавляем
  9-ю, например в пару к Post или на отдельную строку).
- Роутинг в `menu.py`: payload `"image"` → `set_state(user_id, "image")` и просьба
  прислать картинку; триггер при **прямой отправке картинки** — отдельный
  `message_created` хендлер (или ветка в `on_menu_text`), проверяющий
  `_image_urls(event)`.
- Точка интеграции: `app/max/executors.py::do_image_analyze(deps, event, image_bytes)`.

### Псевдокод executors
```python
# app/max/executors.py
async def do_image_analyze(deps: Deps, event, image_bytes: bytes) -> None:
    chat_id, user_id = event.get_ids()
    await event.message.answer("🖼 Разбираю картинку…")
    try:
        answer = await deps.orchestrator.run_vision(
            role="analyzer",
            task="Опиши изображение и ответь на вопрос пользователя.",
            images=[image_bytes],
            context={"source": "max", "entry": "image"},
            chat_id=chat_id, user_id=user_id,
        )
    except VisionUnsupported:
        # fallback: OCR -> обычный анализ
        text = await ocr_image(image_bytes)        # через ocr-and-documents
        answer = await deps.orchestrator.run(role="analyzer",
            task=f"Текст с изображения:\n{text}", context={...},
            chat_id=chat_id, user_id=user_id)
    banner = header("🖼", "IMAGE — РЕЗУЛЬТАТ", [clean_for_max(answer)])
    await _send_long(event, banner, attachments=home_button())
```

> Для поддержки vision в `LLMClient` расширить `chat()` опциональным параметром
> `images: list[bytes] = None` и в `_call_anthropic`/`_call_openai` собирать
> content-array с image-блоками (anthropic: `{"type":"image","source":{...}}`;
> openai: `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}`).

---

## 5. Menu changes

### `app/max/keyboards.py`
В `main_menu_keyboard()` добавить 9-ю кнопку (payload `"image"`):

```python
def main_menu_keyboard() -> list:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="🔍 Research", payload="research"),
          CallbackButton(text="✍️ Copy", payload="copy"))
    b.row(CallbackButton(text="📅 Plan", payload="plan"),
          CallbackButton(text="💡 Ideate", payload="ideate"))
    b.row(CallbackButton(text="🔬 Analyze", payload="analyze"),
          CallbackButton(text="🎯 Prompt", payload="prompt"))
    b.row(CallbackButton(text="📤 Post в канал", payload="post"),
          CallbackButton(text="🖼 Анализ картинки", payload="image"))   # НОВОЕ
    b.row(CallbackButton(text="❓ Помощь", payload="help"))
    return [b.as_markup()]
```

### `app/max/handlers/menu.py`
1. В `on_menu_callback` добавить ветку `payload == "image"`:
   ```python
   if payload == "image":
       set_state(user_id, "image")
       await _menu_reply(event, header("🖼", "АНАЛИЗ КАРТИНКИ",
           ["Пришли картинку — бот её разберёт."]),
           attachments=main_menu_keyboard())
       return
   ```
2. В `on_menu_text` (после `clear_state`) добавить обработку `action == "image"`:
   ```python
   elif action == "image":
       urls = _image_urls(event)
       if urls:
           data = await download_bytes(urls[0])
           await do_image_analyze(deps, event, data)
       else:
           await event.message.answer("⚠️ Картинка не найдена. Пришли файл-изображение.",
                                      attachments=home_button())
   ```
3. Отдельный `message_created` хендлер для **прямой** отправки картинки (без кнопки):
   ```python
   @dp.message_created()
   async def on_direct_image(event: MessageCreated) -> None:
       if get_state(event.get_ids()[1]) is not None:
           return  # меню-флоу сам заберёт
       urls = _image_urls(event)
       if urls:
           data = await download_bytes(urls[0])
           # скачиваем НОВУЮ картинку
           await do_image_analyze(deps, event, data)
   ```
   (`_image_urls` и `download_bytes` вынести в `app/max/image_utils.py`.)

### `app/max/state.py`
Действие `"image"` уже покрывается универсальным `set_state(user_id, "image")`
(FSM хранит `action` как строку). Явное перечисление не обязательно, но для
наглядности можно добавить константу:
```python
ACTION_IMAGE = "image"
```

---

## 6. Implementation order (для Masta Killa)

1. **Storage (раздел 3)**: добавить таблицы `antispam_messages`, `antispam_bans` и
   методы (`add_antispam_message`, `recent_antispam`, `count_in_window`,
   `is_banned`, `add_ban`) в `app/db/storage.py`; вызвать в `_create_tables`.
2. **Config (разделы 3,4)**: в `app/config.py` добавить `antispam_enabled`,
   `antispam_ban_words`, `allow_private_chat`, `max_bot_username`; значения в `.env`.
3. **Antispam middleware (раздел 3)**: создать `app/max/antispam.py`
   (`AntispamMiddleware`), зарегистрировать в `app/max/client.py` ДО основных
   хендлеров; проверить, что админы (`admin_user_ids`) не блокируются.
4. **Group/channel listening (раздел 2)**: создать `app/max/handlers/group_listen.py`,
   зарегистрировать в `register_handlers`; добавить фильтрацию по `chat.type` и
   командам/упоминаниям; проверить ответ `event.message.answer` и публикацию в
   канал через `bot.send_message`.
5. **Skills map (раздел 1)**: добавить `ROLE_SKILLS` и секцию в `_system_prompt`
   в `app/core/orchestrator.py`; убедиться, что нужные `app/llm/prompts/*.py`
   существуют (researcher, copywriter, prompt_engineer, analyzer).
6. **Image utils**: создать `app/max/image_utils.py` (`_image_urls`, `download_bytes`)
   + расширить `LLMClient.chat` параметром `images` (anthropic/openai image-блоки).
7. **Image executor (раздел 4)**: добавить `do_image_analyze` в `executors.py` с
   OCR-фолбэком.
8. **Menu (раздел 5)**: добавить кнопку `image` в `keyboards.py`, ветки в
   `menu.py` (`on_menu_callback`, `on_menu_text`, `on_direct_image`), константу в
   `state.py`.
9. **Проверка vision (раздел 4)**: короткий тест MiniMax-M3 (`/v1/messages` с
   image-блоком) и `GET /v1/models` для StepFun; зафиксировать реальный статус
   поддержки картинок.
10. **Тесты/дока**: unit-тесты на antispam (rate-limit/дубли/бан-слова) и image
    routing; обновить README с описанием новых прав бота (админ в группах,
    подписка на каналы).

---

### Статус поддержки картинок LLM (что реально известно без долгого поиска)
- **MiniMax-M3** (primary, anthropic-style `https://api.minimax.io/anthropic`):
  Anthropic Messages API нативно поддерживает image content blocks ⇒ **вероятно
  поддерживает vision**,但需要 подтвердить тестовым вызовом.
- **StepFun `step-3.7-flash`** (fallback, openai-style `https://api.stepfun.ai/v1`):
  поддержка `image_url` зависит от того, мультимодальна ли модель ⇒ **неизвестно,
  проверить через `GET /v1/models`**.
- Fallback-стратегия при отсутствии vision у обеих моделей: OCR
  (`ocr-and-documents`) → текстовый анализ, либо отдельный vision-provider.
