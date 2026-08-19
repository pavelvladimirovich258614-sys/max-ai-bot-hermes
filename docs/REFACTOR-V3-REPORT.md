# Рефакторинг V3 — финальный технический отчёт

Дата: 2026-08-19  
Проект: `D:\hermes-multi-agent-setup\max-ai-bot`  
Статус: код, автоматический acceptance и Docker/Linux acceptance завершены; визуальный UI click-through после V7 ожидается.

## Что закрыто

### B1–B5

- [✅] B1 — генерация изображений.
  - Корень ошибки найден в FSM: `image_gen._set()` передавал `mode=` в `set_state()`, который не принимал keyword-аргументы.
  - FSM переведён на плоскую запись: `app/max/state.py:14`, `app/max/handlers/image_gen.py:73`.
  - ImageClient: transient-only retry (`app/llm/image_client.py:38`), timeout из Settings, status/request-id/body-excerpt logging (`app/llm/image_client.py:293`), допустимые aspect ratios по документации, гарантированное закрытие HTTP pool.
  - Реальный вызов MiniMax image-01 успешен: JPEG 263356 bytes, SHA-256 `32dec59ef00ef6db36b9b06c03891b5ab369f0f445795ce9e94b5f6852f21076`.
  - Артефакт: `logs/minimax_image_smoke.jpg`.
  - UI-клик в MAX ожидает проверки Павла.

- [✅] B2 — «Мои каналы».
  - Удалена неверная трактовка GET `/subscriptions` как списка каналов.
  - Добавлен SQLite-каталог каналов: `app/db/storage.py:324`, `app/db/storage.py:354`.
  - Добавлены lifecycle handlers `bot_added` / `bot_removed` и outer middleware activity-fallback: если long polling пропустил исторический `bot_added`, следующий event из канала с `recipient.chat_type=channel` регистрирует его без перехвата команд.
  - Если каталог пуст, бот объясняет, что его нужно добавить администратором в канал.
  - Для уже существующих каналов activity-fallback зарегистрирует канал при следующем событии из него; текущий канал восстановлен из подтверждённого runtime-event.

- [✅] B3 — FSM ручного chat_id.
  - Состояние `post:awaiting` теперь сохраняется корректно и не теряет данные.
  - Кнопка отмены очищает state.
  - Покрыто unit/regression тестами; UI-клик ожидает проверки Павла.

- [✅] B4 — plain-text fallback.
  - `MarkdownSender` оставлен как совместимое имя, но по умолчанию отправляет `format=None`: `app/max/formatting.py:95`.
  - Все LLM-ответы проходят через `clean_for_max()` независимо от `MESSAGE_FORMAT`.
  - `/start`, описания меню и 8 role prompts переведены на plain text + ▶/•/✅/⚠️/💡.
  - Прямой `format=markdown` из `/start` удалён.

- [✅] B5 — slash-команды.
  - Runtime log подтвердил регистрацию 11 команд через MAX API.
  - UI-подсказки после ввода `/` ожидают проверки Павла.

### H1–H3 — Hermes integration

- [✅] H1 — кнопка и подменю Hermes присутствуют, три сценария зарегистрированы.
- [✅] H2 — session/dispatcher/storage integration покрыта тестами.
- [✅] H3 — lifecycle исправлен:
  - задача передаётся в CLI одним argv-аргументом без shell injection: `app/hermes/session.py:59`;
  - один `communicate()` task, периодический progress, kill/await на timeout/cancel;
  - supervisor tasks отслеживаются и закрываются: `app/hermes/dispatcher.py:118`;
  - progress Hermes отправляется один раз, затем редактируется по сохранённому `mid`; финальный результат заменяет progress-сообщение вместо создания стопки;
  - предупреждения о незакрытом Windows subprocess transport исчезли.
- UI-тест трёх сценариев ожидает проверки Павла.

### F1–F3

- [✅] F1 — 8 промптов переписаны под B2B-аудиторию (коучи, психологи, юристы, эксперты), TONE OF VOICE, anti-AI stop-list и MAX plain text.
- [✅] F2 — `descriptions.py`, `/start` и пользовательские подсказки отредактированы на естественном русском.
- [✅] F3 / Docker CA — образ собран и проверен внутри Linux-контейнера.

### Сертификат Минцифры в Docker

- [✅] Скачаны официальные RSA-сертификаты со статического домена страницы Госуслуг `gu-st.ru`.
- [✅] ZIP-хеши совпали с опубликованными:
  - root ZIP: `ca99ca9b0022ec8b99d5822502cf3f38d4797bdd02cc098996778421d72d7e24`;
  - sub ZIP: `35d8ce3ed079b1cd3a1650bf2ed2d873eee288799924dbbe128c172b65d3594e`.
- [✅] Dockerfile устанавливает `ca-certificates`, root и RSA Sub CA 2024, затем вызывает `update-ca-certificates`: `Dockerfile:13–17`.
- [✅] Реальная TLS-проверка: TLS 1.3, `platform-api2.max.ru`, `Verification: OK`.
- [✅] Реальная отправка acceptance-чек-листа через HTTPX с этим root CA: HTTP 200.
- [✅] Docker image `max-ai-bot:v3` пересобран с V7 channel recovery: `sha256:fbe94656d87b14ac2ea5de85171f59d7b249ca4dbe64310b8b70be3da40552ee`, 89,926,621 bytes.
- [✅] TLS из контейнера дошёл до ожидаемого `401 Unauthorized` без `CERTIFICATE_VERIFY_FAILED`.
- [✅] Контейнерный pytest: **132 passed**.

## Тесты

- pytest (Windows host): **132 passed**, без warnings.
- pytest (Docker/Linux): **132 passed**, без failures.
- py_compile/compileall: **чисто**.
- MiniMax image-01: **реальный smoke успешен**.
- MAX API TLS: **Verification: OK**.
- MAX acceptance message: **HTTP 200**.

## Runtime

Свежий запуск после остановки прежнего listener:

- health: `GET /health` → 200 `{"status":"ok"}`;
- `Application startup complete`;
- bot: `@id752703975446_3_bot`, id `224141223`;
- slash-команды: 11;
- handlers: 19;
- listener: `0.0.0.0:8080`, server PID 31104;
- лог: `logs/bot_run_channel_registry.log`.

## V6 — подмена сообщений и быстрые callback handlers

- [✅] Обычные переходы меню (`research`, `copy`, `plan`, `ideate`, `analyze`, `prompt`, `help`, `post`, `home`, `restart`) используют один `event.answer(new_text=..., attachments=...)` вместо `ack + send_message`.
- [✅] Hermes и image-подменю используют тот же in-place callback path.
- [✅] `post:approve/reject/edit` сначала заменяют нажатое сообщение; publish/API выполняется только после визуального acknowledgement.
- [✅] Общий menu-router больше не перехватывает `hermes:*` и `image:*`; post action handler игнорирует submenu payloads. Один payload обслуживается ровно одним handler.
- [✅] Hermes progress не создаёт сообщение на каждый шаг: первое обновление отправляется один раз, следующие редактируют его, итог заменяет тот же блок.
- [✅] Добавлено 11 regression-сценариев в `tests/test_callback_navigation.py`; они запрещают новые `send_message`/`send_callback` для навигации и проверяют ранний acknowledgement долгой публикации.
- [⚠️] CUA click-through не завершён: после автоматических тестов процесс MAX оставался запущен, но доступного окна не было (клиент свёрнут/закрыт в трей). Приложение принудительно не выводилось на передний план.

## V7 — надёжное обнаружение уже добавленного канала

- [✅] Диагностика реального случая: MAX прислал `message_edited` из канала `-72143469522347`, но не прислал `bot_added`; поэтому прежний lifecycle-only каталог остался пуст.
- [✅] `ChannelActivityMiddleware` наблюдает все updates до выбора handler и один раз запрашивает название только для явно помеченного MAX канала (`recipient.chat_type=channel`). Команды и callbacks не перехватываются.
- [✅] Текущий канал восстановлен read-only API-вызовом `get_chat_by_id` и записан в SQLite: `{Павел}|Нейросети, ИИ агенты,чат-боты`.
- [✅] Ручная проверка БД после рестарта: 1 active channel. Фоновый CUA-клик в MAX не породил callback, поэтому не учитывается как UI acceptance.

## Новые файлы

- `app/max/handlers/channel_registry.py` — lifecycle-каталог групп/каналов.
- `tests/test_state.py` — FSM regression tests.
- `tests/test_channel_registry.py` — SQLite registry tests.
- `tests/test_channel_registry_handlers.py` — lifecycle/menu registry tests.
- `tests/test_callback_ack.py` — отсутствие пустых callback bubbles.
- `tests/test_callback_navigation.py` — единая подмена меню, строгая маршрутизация и ранний ack долгих кнопок.
- `tests/test_docker_certificates.py` — сертификаты и Dockerfile.
- `certs/russian_trusted_root_ca_pem.crt` — официальный RSA root CA.
- `certs/russian_trusted_sub_ca_2024_pem.crt` — актуальный RSA Sub CA.
- `certs/README.md` — источники, хеши, fingerprints.
- `scripts/smoke_minimax_image.py` — безопасный image smoke.
- `scripts/send_acceptance_checklist.py` — отправка ручного чек-листа с проверяемым TLS.
- `logs/minimax_image_smoke.jpg` — результат реального image-01 smoke.
- `logs/bot_run_v3.log` — свежий runtime log.

## Изменённые файлы

- `docs/REFACTOR-V3-PLAN.md` — актуальный аудит и фазовый план.
- `app/max/state.py`, `app/max/handlers/image_gen.py` — FSM B1 и lifecycle ImageClient.
- `app/llm/image_client.py` — retry, timeout, logging, context manager, validation.
- `app/db/models.py`, `app/db/storage.py` — known_chats registry.
- `app/max/handlers/menu.py`, `app/max/handlers/hermes_button.py`, `app/max/handlers/image_gen.py`, `app/max/handlers/callback_handler.py` — in-place навигация и строгая маршрутизация callback payloads.
- `app/max/formatting.py`, `app/max/executors.py`, `app/max/handlers/start.py` — единый plain-text outgoing path.
- `app/hermes/session.py`, `app/hermes/dispatcher.py`, `app/context.py`, `app/main.py` — безопасный subprocess lifecycle и shutdown.
- `app/max/descriptions.py` — натуральный русский plain text.
- `app/llm/prompts/*.py` — восемь role prompts.
- `Dockerfile` — trust store Минцифры.
- существующие тесты обновлены под подтверждённый plain-text контракт.

## Что осталось

1. Открыть окно MAX и прокликать `Исследовать → Копирайтинг → Hermes → В меню → Перезапуск бота`; визуально должен изменяться один последний menu-блок без новых bubble.
2. Подтвердить отсутствие пустых белых callback-блоков и стопки меню скриншотом после текущего V7-рестарта.
3. Подтвердить image flow, manual chat_id, slash menu и три Hermes-сценария в MAX UI.
4. Для production переключить transport с polling на webhook и проверить CA/secret на Ubuntu VPS. Это отдельный deploy acceptance, не закрытый локальным polling-тестом.
5. Group listening, входящее image vision и antispam остаются отдельными фичами — в этот bug-fix/refactor проход не добавлялись.

## Итоговый статус

Автоматическая часть V3/V7 подтверждена 132 тестами на Windows и в Docker/Linux, API-вызовами и свежим runtime-логом. Docker-блокер закрыт. Релиз не помечен окончательно «готов»: остаётся только визуальный click-through в открытом окне MAX.
