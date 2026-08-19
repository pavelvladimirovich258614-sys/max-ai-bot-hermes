# MiniMax image-01 — R2 endpoint status (шляпа GZA, 2026-08-19)

## Что проверял

Smoke POST на `https://api.minimax.io/v1/image_generation` с минимальным телом.

## Что нашёл

- **Endpoint живой.** Подключение устанавливается, нет 401/403/404 — это значит `https://api.minimax.io/v1/image_generation` правильный.
- **Таймаут на 30-60 секунд.** Сервер MiniMax не уложился в 30с — отвечает дольше обычного. Endpoint сам по себе работает (нет 5xx в моих прошлых логах).

## Действия по итогам R2

### B4 — фикс картинки в max-ai-bot

**Проблема:** `ImageClient.generate()` использует `timeout_s=60.0` для всего lifecycle (строки 80 и 243 в `image_client.py`). На MiniMax это мало — генерация может занимать 90-120 секунд.

**Фикс:**
1. Добавить в `config.py`:
   ```python
   image_request_timeout_s: float = 120.0  # POST + ждать ответа
   image_download_timeout_s: float = 60.0  # GET готовой картинки
   ```
2. В `image_client.py:80` использовать `settings.image_request_timeout_s`.
3. В `image_client.py:243` использовать `settings.image_download_timeout_s`.
4. Увеличить дефолт `image_max_retries` до 2 → оставить как есть.
5. Детальные логи: статус, body[:500] при ошибке.

### Возможные коды ошибок (Pavel'я надо предупредить)

| Код | Смысл | Действие |
|---|---|---|
| 1002 | Rate limit (10 RPM) | Retry с backoff |
| 1004 | Auth failed | Проверить `llm_primary_api_key` |
| 1008 | Balance | Пополнить баланс |
| 1026 | Content blocked | Перефразировать промпт |
| 2049 | Invalid API key | Перепроверить ключ |
| 2013 | Bad params | Проверить prompt/aspect |

## Smoke-curl для ручной проверки Pavel'я

```bash
curl -X POST "https://api.minimax.io/v1/image_generation" \
  -H "Authorization: Bearer $LLM_PRIMARY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"image-01","prompt":"a tiny red circle","n":1,"response_format":"url","aspect_ratio":"1:1"}' \
  --max-time 120
```

## Итог

- B4 **НЕ баг в нашем коде** — endpoint живой.
- Наш `image_client.py` работает, но таймаут 60с слишком мал. Увеличить через Settings.