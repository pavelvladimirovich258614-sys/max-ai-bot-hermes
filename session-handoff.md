# session-handoff.md — точка восстановления

> Этот файл обновляется **в конце каждой сессии** перед `git commit`.
> В начале следующей сессии — прочитай его первым.

---

## Last verified: 2026-08-22 22:48 UTC (сессия 007 — batch 3 merged, deploy pending)

### Проверено сейчас ✅
- **HEAD на main:** `4cc68e8` (harness, до batch 3 merge)
- **Batch 3 worktree'ы:** `b3-A` @ `ac1aaca`, `b3-B` @ `a1ab1a5`, `b3-C` @ `8e5c0df` — все clean
- **Patch apply:** A → C → B, без конфликтов, 9 новых + 1 modified (storage.py)
- **Тесты локально:** **250 passed** (231 baseline + 19 новых: 8 pipeline + 4 evaluator + 7 cache)
- **pytest collect-only:** 250 tests, 0 errors
- **Прод HEAD до deploy:** `d74c350` (предыдущий hotfix merge)
- **`/healthz` на проде:** ещё 200 OK от deploy hotfix C
- **Live smoke ранее:** PARTIAL, 8 findings, все `published_at=2026-08-15` (≤7d окно)

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
