# progress.md — история сессий max-ai-bot-hermes

> Заполняется в конце каждой сессии. Перед началом сессии — прочитай последнюю запись + `session-handoff.md`.

## 001 — 2026-08-21: F0 (fallback chain + /status + intent router)

- **Что:** Трёхуровневый fallback в Orchestrator, команда `/status`, free-chat intent router
- **Ветка:** `feature/hotfix-fallback-and-router`
- **Тесты:** 186 passed (151 baseline + 35 new)
- **Артефакт:** `max-ai-bot-hotfix-F0/SUMMARY.md`
- **Статус:** ✅ done

## 002 — 2026-08-21: F1 (/prompt как Context Engineer)

- **Что:** `/prompt` переделан в Senior Context Engineer, 8 доменов, матрица домен→навык, обвязка с DoD/SCOPE/STEPS/CONSTRAINTS
- **Тесты:** 214 passed (186 + 28 new)
- **Артефакт:** `max-ai-bot-hotfix-F0/SUMMARY-F1.md`
- **Статус:** ✅ done

## 003 — 2026-08-21: F2 (research freshness + cascade + strict JSON)

- **Что:** Freshness window, каскад Tier 1/2/3, Pydantic schema, Hermes bg enrichment, синтаксис `[Nd]`
- **Тесты:** 227 passed (214 + 20 new − 7 retiered)
- **Артефакт:** `max-ai-bot-hotfix-F0/SUMMARY-F2.md`
- **Статус:** ✅ done (mock smoke only, прод-smoke отложен)

## 004 — 2026-08-22: Deploy F0+F1+F2 на прод (ночь)

- **Что:** `git checkout -b feature/f0-f1-f2-bundle`, merge в main (`4108519`), deploy через docker compose
- **Smoke:** S6 (research) — 8 findings, status=PARTIAL, exit=1, MiniMax + DuckDuckGo работают
- **Health:** `/healthz=200` на 1-й попытке, 12 commands + 21 handlers registered
- **Прервано:** S7 (status smoke) не доделан, S8/S9 отложены
- **Артефакт:** `max-ai-bot-hotfix-F0/CHECKPOINT.md`, `after-smoke-research.txt`
- **Статус:** ✅ done (deploy green, мелкие шаги отложены)

## 005 — 2026-08-22: Hotfix C (BUG #1 + BUG #2) + deploy

- **Что:** BUG #1 (parse_freshness import в `research_smoke.py`), BUG #2 (`_compose_unknowns` условный)
- **Ветка:** `hotfix/research-smoke-bugs` → `main` (--no-ff merge)
- **Коммиты:** `5f4eba1` (fix) + `d74c350` (merge)
- **Тесты:** 231 passed (227 + 4 new: 3 для BUG #2, 1 для BUG #1)
- **Deploy:** `down` + `up -d --build` — успешно
- **Health:** `/healthz=200` 1st attempt
- **Smoke:** PARTIAL, 8 findings, все `published_at=2026-08-15` (≤7d), 5.6s
- **Артефакты:** `after-ps-hotfix.txt`, `after-date-hotfix.txt`, `after-logs-hotfix.txt` в `max-ai-bot-hotfix-F0/`
- **Side-effect:** `maxapi>=0.13.0` → `maxapi==1.2.2` (pin в requirements.txt, был не установлен локально)
- **Статус:** ✅ done

## 006 — 2026-08-22: Harness files (in progress)

- **Что:** AGENTS.md, feature_list.json, init.sh, progress.md (этот файл), session-handoff.md
- **Цель:** Single source of truth для следующих сессий
- **Текущее состояние:** main HEAD = `d74c350`, /healthz=200, бот на проде ~20h healthy
- **Следующий:** Батч 3 — Pipeline orchestrator + Evaluator (golden set) + SQLite cache
- **Статус:** 🔄 in progress

---

## Convention: новая сессия

1. Прочитай `session-handoff.md` — текущее состояние и следующий шаг
2. Проверь `git log --oneline -3` — HEAD должен совпадать
3. Запусти `bash init.sh` (или `python -m pytest tests/ -q`) — должен пройти
4. Если что-то не совпадает — СТОП, доложи пользователю
5. Сделай свою работу
6. В конце сессии: обнови `session-handoff.md` + добавь запись в `progress.md`
