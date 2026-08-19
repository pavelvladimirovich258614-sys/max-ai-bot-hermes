# MAX AI Bot — Handoff на 2026-08-19

## Контекст

Бот: `@id752703975446_3_bot` в мессенджере MAX.
Проект: `D:\hermes-multi-agent-setup\max-ai-bot\`
Стек: Python 3.12, `maxapi` SDK, FastAPI+uvicorn, httpx, pydantic-settings, aiosqlite.
Админ: Pavel (id 73412011, id чата 154939916).

---

## ✅ Что работает (на момент паузы)

| Фича | Статус |
|------|--------|
| Бот стартует в polling на 8080 | ✓ живой |
| `/start` → баннер-картинка + описание + 9 кнопок | ✓ |
| Inline-кнопки меню работают (без 400 «message=None») | ✓ |
| ProgressReporter (шаги в чате) | ✓ в проде |
| `_safe_orchestrator_run` (timeout 60с + try/except) | ✓ |
| `_send_long` с задержкой 0.5с между чанками | ✓ |
| Копирайтер с 4 режимами (ТЕМА/ЧЕРНОВИК/ССЫЛКА/ДОКУМЕНТ) | ✓ в проде |
| `_fetch_url_text` через WebReader для URL | ✓ |
| Все 6 ролей получили промпты с РЕЖИМАМИ | ✓ |
| Кнопка `🔄 Перезапуск бота` (шлёт /start в чат) | ✓ |
| `clean_for_max()` (MAX не рендерит markdown) | ✓ |
| Skill-карта в оркестраторе (4 роли × навыки Hermes) | ✓ |
| `attach_local_image` (async) — баннер на `/start` | ✓ |
| pytest 36/36, py_compile чистый | ✓ |
| `.env` заполнен реальными токенами Pavel'я | ✓ |
| Dockerfile + docker-compose (для прод-деплоя) | ✓ |

---

## ⚠️ Известные проблемы / не сделано

| Проблема | Где | Что делать |
|----------|-----|------------|
| **Контент-план серый, без эмодзи/выделений** | LLM (marketer.py) | Ужесточить SYSTEM_PROMPT (явные примеры структуры с эмодзи-разделителями), возможно поднять temperature |
| **Копирайтер всё ещё AI-шный** | LLM (copywriter.py) | Расширить humanizer-секцию: список конкретных AI-измов для удара, примеры «плохо/хорошо» |
| **Картинка-пост через `do_post`** | UI (publisher.py) | Публикует только текст, без картинки; можно добавить опциональное фото |
| **Antispam** | handlers/middleware | GZA дал дизайн, Masta Killa сделал только таблицы в storage.py; хендлера/middleware нет |
| **Group/channels listening** (`group_listen.py`) | не создан | По спеке GZA — реагировать на /команды и упоминания в группах |
| **Image handler** (`image_handler.py`) | не создан | Принять прикреплённую картинку, скачать, отдать в LLM vision (MiniMax-M3 поддерживает image content blocks) |
| **Сертификат Минцифры в Docker** | Dockerfile | Прод-блокер: на голом `python:3.12-slim` рукожатие с `platform-api2.max.ru` падает (Verify 20); добавить CA в образ |
| **Webhook** | main.py | Не тестирован; код есть, prod-сертификат надо подтвердить |
| **Тест бота через реальный клик** | — | Pavel тестирует руками; автоматического e2e нет |

---

## 🛠 Технические хитрости

1. **Убить uvicorn на Windows:**
   ```powershell
   Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
   ```
   `pkill` через bash НЕ работает (CP866 + .exe).

2. **`attach_local_image`**: возвращает `[upload]` (готовый `AttachmentUpload`), НЕ `[Attachment(type=..., payload=upload.payload)]` — pydantic discriminator отвергает базовый `AttachmentPayload`.

3. **`send_callback`** в MAX обязательно `message=MessageForCallback(...)`, не `None`. Иначе 400 «message or notification required».

4. **`attachments=` всегда list**. `event.message.answer(...)` ждёт `[markup]` (list), а `send_callback` ждёт `MessageForCallback(attachments=[markup])`.

5. **`/start` ломается при IndentationError** — патч через `patch` tool иногда ломает отступы. Для таких файлов — `write_file` с полным содержимым.

6. **MAX не рендерит markdown** — весь вывод через `clean_for_max()` (строки `app/max/ui.py`).

---

## 📂 Ключевые файлы

```
app/max/handlers/
  start.py        — /start с баннером + меню
  menu.py         — callback routing, FSM state, restart
  free_chat.py    — обычный диалог с прогрессом
app/max/
  keyboards.py    — main_menu_keyboard (9 кнопок), post_*, callback_message()
  executors.py    — run_role/do_*/с ProgressReporter + timeout + retry
  ui.py           — header(), clean_for_max(), chunk_text(), ProgressReporter, attach_local_image()
  bot_wrapper.py  — CompliantBot (домен v2, токен через Authorization)
app/llm/prompts/
  researcher.py, copywriter.py (8.5К — самый толстый), marketer.py,
  ideator.py, analyzer.py, prompt_engineer.py, chat.py
app/core/orchestrator.py — _system_prompt(role) + ROLE_SKILLS dict
docs/
  enhancement-design.md  — спека от GZA (слушание чатов, антиспам, картинки)
  HANDOFF.md              — этот файл
```

---

## 🚀 Стартовый промпт для новой сессии

Скопируй в новый чат (адаптируй под дату сессии):

```
Контекст компактный (читай до ответа):
- Проект: D:\hermes-multi-agent-setup\max-ai-bot (MAX-бот, Python 3.12, maxapi SDK)
- Бот: @id752703975446_3_bot в MAX; админ Pavel (user_id 73412011, chat_id 154939916).
- Состояние на 2026-08-19 (последняя сессия): pytest 36/36, бот жив на 8080 polling,
  /start шлёт баннер+меню, 9 кнопок работают, ProgressReporter + timeout 60с в проде,
  копирайтер поддерживает 4 режима (ТЕМА/ЧЕРНОВИК/ССЫЛКА/ДОКУМЕНТ), кнопка 🔄 Перезапуск
  бота работает. Полный handoff в D:\hermes-multi-agent-setup\max-ai-bot\docs\HANDOFF.md.
- Нерешённое (см. HANDOFF.md): контент-план серый без эмодзи (ужесточить SYSTEM_PROMPT
  marketer.py), копирайтер всё ещё AI-шный (расширить humanizer-секцию copywriter.py),
  НЕ реализованы: group_listen.py (слушание чатов/каналов), image_handler.py
  (приём картинок + LLM vision), антиспам (только таблицы в storage.py), сертификат
  Минцифры в Dockerfile для прод-деплоя. Masta Killa несколько раз обрывался по лимиту
  итераций (HTTP 429/529) — большие задачи разбивать.

Что делаем сейчас: [ОПИШИ ЗАДАЧУ]

Что НЕ делаем:
- НЕ трогаем .env (реальные секреты Pavel'я), bot_wrapper.py (домен v2, токен через
  Authorization), config.py (имена полей MAX_BOT_TOKEN/LLM_PRIMARY_API_KEY/etc),
  keyboards.py и menu.py (если только это не задача про кнопки), start.py (там баннер).
- НЕ выдумываем токены/ключи — берём только из .env или сессии Pavel'я.
- НЕ добавляем LangChain/LiteLLM/Redis/PostgreSQL/FSM-фреймворков.

Прежде чем кодить — прочитай HANDOFF.md и существующий код. Перезапускай бота
через PowerShell kill (pkill на Windows не работает).
```

---

## ✅ Чек-лист «бот готов к тесту»

После любой правки прогоняй:

```bash
cd /d/hermes-multi-agent-setup/max-ai-bot

# 1. Скомпилируется
python -m py_compile $(find app tests -name '*.py')

# 2. Тесты зелёные
python -m pytest -q

# 3. Убить старый бот
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force }"
sleep 3
powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort 8080 -State Listen | Measure-Object).Count"
# Должно быть 0

# 4. Запустить
. .venv/Scripts/activate
nohup uvicorn app.main:app --host 0.0.0.0 --port 8080 --log-level info > logs/bot_run.log 2>&1 &
sleep 10
tail -15 logs/bot_run.log
# Должны быть: Application startup complete + Бот: @id... + 13 хендлеров

# 5. В MAX кликнуть /start → баннер + 9 кнопок
# 6. Кликнуть по каждой кнопке, дождаться ответа (7-12с на LLM)
# 7. Проверить /help → fallback команды
# 8. 🔄 Перезапуск бота → в чат уходит /start → бот отвечает меню
```

---

## 🐛 Если что-то сломалось — быстрая диагностика

```bash
# Лог живого бота
tail -30 /d/hermes-multi-agent-setup/max-ai-bot/logs/bot_run.log

# Какие хендлеры зарегистрированы
grep "Зарегистрировано" /d/hermes-multi-agent-setup/max-ai-bot/logs/bot_run.log | tail -3

# Сколько процессов на порту
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8080 -State Listen | Select-Object OwningProcess"

# Если кнопки залипают (финал не доходит) — посмотреть последние llm_call
grep "llm_call\|ERROR\|Traceback" /d/hermes-multi-agent-setup/max-ai-bot/logs/bot_run.log | tail -20

# Если картинка не пришла в /start — ищем validation errors
grep "validation error\|attach_local_image failed" /d/hermes-multi-agent-setup/max-ai-bot/logs/bot_run.log | tail -10
```

---

## 📞 Контакты

- Pavel: user_id=73412011, chat_id=154939916, MAX @id752703975446_3_bot
- LLM primary: MiniMax-M3 (anthropic-style, https://api.minimax.io/anthropic)
- LLM fallback: StepFun step-3.7-flash (openai-style, https://api.stepfun.ai/v1)
- Webhook URL (когда понадобится): https://max.ai-agent-paul.ru/webhook/max
- Prod CA Минцифры: https://www.gosuslugi.ru/crt