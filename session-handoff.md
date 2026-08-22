# session-handoff.md — точка восстановления

> Этот файл обновляется **в конце каждой сессии** перед `git commit`.
> В начале следующей сессии — прочитай его первым.

---

## Last verified: 2026-08-22 21:08 UTC (сессия 006)

### Проверено сейчас ✅
- **HEAD на main:** `d74c3506e67e504758ac8c76fb1ee68ed377da1c`
- **Предыдущий:** `5f4eba1` (hotfix commit) → `4108519` (F0+F1+F2 merge)
- **`/healthz` на проде:** `200 OK`, body `{"status":"ok"}`
- **docker compose ps:** `max-ai-bot Up ~1 min (healthy)`, image `max-ai-bot-hermes-max-ai-bot:latest`
- **Бот uptime:** ~20 минут (rebuild в 13:06 UTC)
- **Live smoke:** PARTIAL, 8 findings, все `published_at=2026-08-15` (≤7d окно)
- **Тесты локально:** 231 passed in 22.20s
- **maxapi pin:** `maxapi==1.2.2` в `requirements.txt`
- **Harness files:** 5 новых (AGENTS.md, feature_list.json, init.sh, progress.md, session-handoff.md) — коммитятся в этом хандоффе

### Изменено в последней сессии (006 — harness)
- Создано 5 harness-файлов (см. `progress.md` сессия 006)
- Никаких изменений в `app/`, `tests/`, `requirements.txt`, deploy-инфраструктуре

### Изменено в предыдущей сессии (005 — hotfix C)
- `app/cli/research_smoke.py` — добавлен module-level import `parse_freshness`
- `app/core/research_cascade.py` — `_compose_unknowns` теперь условный
- `requirements.txt` — `maxapi>=0.13.0` → `maxapi==1.2.2`
- `tests/test_compose_unknowns.py` — новый, 3 теста
- `tests/test_research_smoke_imports.py` — новый, 1 тест
- `patches/H1-...patch`, `H2-...patch`, `H3-...patch` — архивные патчи

### Сломано / известные ограничения 🚨
- ⚠️ `RuntimeWarning: duckduckgo_search renamed to ddgs` — варнинг, не блокер. Известно. Фикс: `requirements.txt` `ddgs` вместо `duckduckgo_search`. Отдельный мини-hotfix если попросят.
- ⚠️ F2.4 (Hermes enrichment) — `skipped` на проде, потому что peer RZA не зарегистрирован в `session.py`. Это by design, graceful degradation.
- ⚠️ `.review-venv/` (60 МБ) в `D:\hermes-multi-agent-setup\max-ai-bot-hermes-review\` — leftover от review-сессии, не в git. Permission-gate блокирует `Remove-Item`. Удалить вручную если мешает.
- ⚠️ Coverage не измерен (D.2 not_started)

### Следующий шаг → Sub-task A (Batch 3)
**Pipeline orchestrator** — `app/core/pipeline_orchestrator.py` (новый файл)
- `hermes peer dm rza "research topic"` как отдельный bash-pipeline для сложных multi-step тем
- Триггер: `len(findings) < TIER1_STOP_THRESHOLD` ИЛИ topic содержит "compare"/"analyze"/"сравни"
- Fallback на текущий cascade если subprocess падает
- Тесты: 3 unit (pipeline construction, trigger detection, fallback)
- **Параллельно:** Sub-task B (Evaluator, golden set 5 тем) и Sub-task C (SQLite cache, TTL 1h)

### Команды для следующей сессии

```bash
# 1. context
cd D:\hermes-multi-agent-setup\max-ai-bot-research-cascade
git status  # ожидаемо: clean
git log --oneline -3  # ожидаемо: HEAD = d74c350 (или новый harness commit)
bash init.sh  # должен пройти (pytest -q)

# 2. SSH на прод (если нужна диагностика)
$sshArgs = "ssh -i $env:USERPROFILE\.ssh\id_ed25519_deploy -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10" -split ' '
& $sshArgs[0] @($sshArgs[1..($sshArgs.Length-1)] + @('root@82.39.213.82')) "cd /opt/max-ai-bot-hermes && git rev-parse HEAD && docker compose ps && curl -s http://127.0.0.1:8080/health"

# 3. smoke через CLI
& $sshArgs[0] @($sshArgs[1..($sshArgs.Length-1)] + @('root@82.39.213.82')) "cd /opt/max-ai-bot-hermes && timeout 70 docker compose exec -T max-ai-bot python -m app.cli.research_smoke --topic 'smoke check' --fresh 7d"
# Ожидаемо: exit=0 или 1, JSON с findings, duration <60s
```

### Не забыть в конце сессии
1. `git add -A && git commit -m "..."` для изменений
2. `git push` ТОЛЬКО если пользователь явно OK
3. `docker compose down + up -d --build` ТОЛЬКО если пользователь явно OK + с rollback планом
4. Обновить `session-handoff.md` (этот файл) — новые "Last verified", "Изменено", "Сломано", "Следующий шаг"
5. Добавить запись в `progress.md` — сессия 007, 008, …
6. Никаких force-push, никаких секретов в чате/файлах/коммитах
