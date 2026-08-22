# session-handoff.md — точка восстановления

> Этот файл обновляется **в конце каждой сессии** перед `git commit`.
> В начале следующей сессии — прочитай его первым.

---

## Last verified: 2026-08-22 23:55 UTC (сессия 009 — OBS-1 deployed, /research investigating)

### Проверено сейчас ✅
- **HEAD на main:** `8673710` (OBS-1 observability)
- **Прод HEAD:** `8673710` (matches main)
- **Тесты локально:** **261 passed** (255 baseline + 6 caplog-тестов на observability)
- **`/healthz` на проде:** 200 OK
- **`webhook_in`/`webhook_out`** на проде подтверждены live curl'ом (msg_type=command, chat_id, user_id, latency_ms)
- **`cascade_start`/`cascade_done`/`cascade_failed`** в коде + 6 caplog-тестов (сработают при реальном MAX webhook'е)
- **MAX_USE_PIPELINE:** false (default), **MAX_RESEARCH_EVAL_ENABLED:** false (default) — opt-in features не активированы

### Изменено в последней сессии (009 — OBS-1)
- `app/main.py` — `webhook_in`/`webhook_out` structured logs (msg_type, chat_id, user_id, latency_ms)
- `app/core/research_cascade.py` — `cascade_start`/`cascade_done`/`cascade_failed` (topic, tier_reached, findings_count, status, stage)
- `tests/test_observability.py` — 6 caplog-тестов
- Harness: `feature_list.json` (D.1 → partial), `progress.md` (009), `session-handoff.md` (этот файл)

### Изменено в предыдущей сессии (008 — Sub-task D)
- `app/core/research_cascade.py` — `run_research_cached()` с `@cache(ttl=3600)`
- `app/max/handlers/research.py` — переписан, `cmd_research` на module level
- `app/config.py` — `max_use_pipeline`, `max_research_eval_enabled` (default false)
- `tests/test_workflow_integration.py` — 5 новых тестов

### 🔴 Известная проблема (next session)
**`/research` через MAX-клиент зависает** — webhook приходит (200 OK в docker logs), но response не доходит до юзера. Раньше нельзя было диагностировать, теперь с OBS-1 logs можно: смотрим `webhook_in` (chat_id, user_id) → correlation с `cascade_start` (topic) → `cascade_done` (status) или `cascade_failed` (stage + traceback). Юзер должен протестировать /research в MAX-клиенте, потом мы читаем `docker compose logs --since=10m | grep webhook_in` и находим update_id → проверяем что произошло дальше.

### Сломано / известные ограничения 🚨
- ⚠️ `MAX_USE_PIPELINE=false` и `MAX_RESEARCH_EVAL_ENABLED=false` на проде — by design (opt-in).
- ⚠️ `RuntimeWarning: duckduckgo_search renamed to ddgs` — варнинг, не блокер.
- ⚠️ F2.4 (Hermes enrichment) — `skipped` на проде, peer RZA не зарегистрирован. By design.
- ⚠️ `.review-venv/` (60 МБ) в `max-ai-bot-hermes-review/` — leftover. Permission-gate блокирует `Remove-Item`.
- ⚠️ Coverage не измерен (D.2 not_started)
- ⚠️ `live_smoke.txt` (untracked в workspace) — мой диагностический артефакт. Можно удалить.

### Изменено в последней сессии (007 — batch 3)
- Sub-task A: `app/core/pipeline_state.py`, `app/core/pipeline_orchestrator.py`, `tests/test_pipeline_orchestrator.py`
- Sub-task B: `app/llm/evaluator.py`, `app/llm/evaluator_schemas.py`, `tests/test_research_evaluator.py`, `tests/fixtures/eval_golden_set.json`, `pytest.ini`
- Sub-task C: `app/db/research_cache.py`, `tests/test_research_cache.py`, `app/db/storage.py` (extended)
- Harness: `feature_list.json` (F3 passing), `progress.md` (сессия 007), `session-handoff.md` (этот файл)

### Изменено в предыдущей сессии (006 — harness)
- 5 harness-файлов: AGENTS.md, feature_list.json, init.sh, progress.md, session-handoff.md
- Коммит `4cc68e8`

### Сломано / известные ограничения 🚨
- ⚠️ `RuntimeWarning: duckduckgo_search renamed to ddgs` — варнинг, не блокер. Известно. Фикс: `requirements.txt` `ddgs` вместо `duckduckgo_search`. Отдельный мини-hotfix если попросят.
- ⚠️ F2.4 (Hermes enrichment) — `skipped` на проде, peer RZA не зарегистрирован в `session.py`. By design, graceful degradation.
- ⚠️ `.review-venv/` (60 МБ) в `max-ai-bot-hermes-review/` — leftover. Permission-gate блокирует `Remove-Item`. Удалить вручную если мешает.
- ⚠️ Coverage не измерен (D.2 not_started)
- ⚠️ Pipeline / Evaluator / Cache **НЕ интегрированы в существующие handlers** — out of scope батча 3. Лежат готовые к использованию, но handlers по-прежнему зовут cascade напрямую. Следующий шаг: Sub-task D (workflow integration) — подключить pipeline к `/research` handler, evaluator в опциональный pre-publish step, cache как decorator на cascade.run.

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
