# Pavel's max_hermes_agent_new — R3 (шляпа GZA, 2026-08-19)

## Клонирование

```bash
git clone https://github.com/pavelvladimirovich258614-sys/max_hermes_agent_new.git \
  D:\hermes-multi-agent-setup\research\max_hermes_agent_new
```

✅ Репозиторий публичный, клонируется.

## Структура

```
max_hermes_agent_new/
├── plugin/max/
│   ├── adapter.py             (1804 строк — основной плагин для Hermes Gateway)
│   ├── plugin.yaml            (манифест)
│   ├── role_registry.yaml     (/copy, /dev, /marketing, /prompt)
│   ├── role_registry.example.yaml
│   └── team_manager/core.py   (/team-add, /team-confirm)
├── examples/profiles/         (SOUL.md примеры для ролей)
└── docs/                      (ARCHITECTURE.md, COMMANDS.md, INSTALL.md)
```

## Что это

**Это НЕ альтернативный бот — это GATEWAY PLUGIN** для hermes-agent (наш основной сеанс). Ставится в `~/.hermes/plugins/max/`.

| Аспект | Pavel's подход | Наш подход |
|---|---|---|
| Где живёт | `~/.hermes/plugins/max/` (gateway) | `app/` (FastAPI max-ai-bot) |
| Запуск | `hermes gateway run` | `uvicorn app.main:app` |
| Роли | `channel_prompt` injection (SOUL.md → ephemeral system prompt) | `app.llm.prompts.<role>` (статические .py) |
| Hermes | RZA/GZA/Cappadonna как отдельные профили | `app.hermes.client.HermesClient` (HTTP+CLI) |

## Что полезного можно взять

1. **role_registry.yaml → channel_prompt pattern** — замена нашему `app.core.orchestrator._system_prompt(role)`.
2. **`/sethome`, `/menu`, `/roles` adapter-level intercepts** — паттерн для локализованных help-текстов.
3. **`force_plain` parameter в `_standalone_post_one`** — fallback на plain text.
4. **`MAX_SEND_CHUNK_SIZE = 3900`** — подтверждение лимита 4000.

## Что НЕ подходит нам

1. Gateway plugin требует установки в `~/.hermes/plugins/max/` + `hermes gateway` — это другой deployment flow.
2. SOUL.md файлы требуют переноса всей инфраструктуры профилей.
3. Multi-agent routing через gateway RZA сейчас не работает (`Hermes CLI rc=1 err="No peer named 'rza'"`) — даже Pavel's plugin столкнётся с этим.

## Рекомендация

**Использовать Вариант B из `hermes-integration-design.md`** — in-process dispatcher с LLM-fallback. Pavel's repo полезно изучить для будущих фич, но НЕ копировать напрямую.

## Детальный анализ

См. `docs/research/hermes-integration-design.md` (717 строк) — полный разбор 3 вариантов реализации с skeletons, трудоёмкостью, trade-offs.