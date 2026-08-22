# AGENTS.md — max-ai-bot-hermes

> Этот репозиторий рассчитан на длительную работу с агентом-кодером.
> Перед началом сессии прочитай [session-handoff.md](session-handoff.md).

## Стек

- **Язык:** Python 3.12
- **Фреймворк:** FastAPI + uvicorn
- **БД:** aiosqlite
- **Валидация:** pydantic v2 + pydantic-settings
- **SDK MAX:** maxapi 1.2.2
- **LLM:** MiniMax-M3 (primary) + StepFun (fallback, опционально)
- **Поиск:** duckduckgo_search, trafilatura, scrapling
- **Контейнеризация:** Docker, docker compose

## Окружение

- **SSH прод:** `$env:USERPROFILE\.ssh\id_ed25519_deploy` (без пароля, ed25519)
- **Прод-хост:** `root@82.39.213.82`, директория `/opt/max-ai-bot-hermes`
- **Локальный workspace:** `D:\hermes-multi-agent-setup\max-ai-bot-research-cascade`
- **Прод порт:** `127.0.0.1:8080` (localhost only)

## Команды

### Verify (обязательно перед каждым коммитом)
```bash
python -m pytest tests/ -q   # 231 passed на момент harness
python -m py_compile $(find app -name "*.py")
curl -s http://127.0.0.1:8080/health
```

### Deploy (на прод)
```bash
# 1. snapshot
ssh -i ~/.ssh/id_ed25519_deploy root@82.39.213.82 \
  "cd /opt/max-ai-bot-hermes && git rev-parse HEAD && docker compose ps > /tmp/before-ps.txt"

# 2. down + rebuild
ssh -i ~/.ssh/id_ed25519_deploy root@82.39.213.82 \
  "cd /opt/max-ai-bot-hermes && docker compose down && docker compose up -d --build"

# 3. health-check loop (max 30s)
for i in 1 2 3 4 5 6; do
  sleep 5
  STATUS=$(ssh ... "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health")
  [ "$STATUS" = "200" ] && break
done

# 4. rollback на fail: git checkout HEAD~1 + down + up
```

### Smoke (без Python-инлайна, через CLI)
```bash
ssh ... "cd /opt/max-ai-bot-hermes && \
  docker compose exec -T max-ai-bot python -m app.cli.research_smoke \
    --topic 'smoke' --fresh 7d"
# Ожидаемо: exit=0 или 1, JSON с findings, published_at в окне свежести
```

## Структура harness (single source of truth)

| Файл | Назначение |
|---|---|
| [AGENTS.md](AGENTS.md) | Этот файл — стартовая инструкция |
| [feature_list.json](feature_list.json) | Список фич + статусы (passing/not_started) |
| [progress.md](progress.md) | История сессий (001, 002, …) |
| [session-handoff.md](session-handoff.md) | Текущее состояние + следующий шаг |
| [init.sh](init.sh) | Автоматическая проверка готовности репо |

## Definition of Done (для любого изменения)

1. ✅ Код работает локально (pytest -q проходит)
2. ✅ py_compile чистый
3. ✅ В CHANGED написано ЧТО и ЗАЧЕМ
4. ✅ В DIDN'T TOUCH написано что НЕ трогал и почему
5. ✅ Секреты НЕ в коде/коммитах/чате
6. ✅ Никаких "я предполагаю" — только подтверждённые факты

## Что НЕ делать

- ❌ Не коммитить `.env`, `.review-venv/`, `__pycache__/`, `*.pyc`
- ❌ Не делать force-push в main
- ❌ Не делать `docker exec ... python -c "..."` (зависает на init_context)
- ❌ Не редактировать `config.py` поля без согласования
- ❌ Не менять `bot_wrapper.py` (CompliantBot, домен v2)
- ❌ Не выдумывать источники / цитаты / версии

## Иерархия фич

```
B0 (infrastructure) — F0 ✅ → F1 ✅ → F2 ✅ → Hotfix C ✅
B1 (intelligence)   — F3 Pipeline + Evaluator
B2 (quality)        — D.1 Observability → D.2 Coverage → D.3 E2E
```

Подробности в [feature_list.json](feature_list.json).
