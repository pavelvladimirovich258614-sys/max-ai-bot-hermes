# MAX API — R1 markdown format (шляпа GZA, 2026-08-19)

## Что проверял

Три варианта отправки одного и того же текста в chat Pavel'я (154939916):

1. `format="markdown"` + текст с `**bold**` `_italic_` `[link](url)`
2. `format="html"` + текст с `<b>bold</b>` `<i>italic</i>` `<a href="url">link</a>`
3. Без `format` (plain) + простой текст

Все три отправки вернули `SendedMessage` (HTTP 200), без 400/422.

## Что нашёл в исходниках maxapi

`maxapi/methods/send_message.py:163-164`:
```python
if self.format is not None:
    json["format"] = self.format
```

`maxapi/bot.py:351-368`:
```python
async def send_message(
    self,
    chat_id: int | None = None,
    user_id: int | None = None,
    text: str | None = None,
    attachments: list[...] | None = None,
    link: NewMessageLink | None = None,
    format: TextFormat | None = None,    # ← ВОТ ОН
    parse_mode: ParseMode | None = None, # ← deprecated, PATCH прекращается 15.09.2026
    *,
    notify: bool | None = None,
    ...
```

`parse_mode` deprecated → использовать `format`. Алиас через `TextFormat = ParseMode = StrEnum`.

## Вердикт

- **Имя параметра:** `format` (НЕ `parse_mode`).
- **Значения:** `"markdown"` / `"html"` / `Format.MARKDOWN` / `Format.HTML` (StrEnum).
- **`format=markdown` принимается SDK и доходит до API** — HTTP 200.
- **Рендер в MAX UI** — особенность платформы. По прошлому скриншоту Pavel'я, в `**жирный**` приходят литералами внутри inline-callback ответов. Без keyboard — рендерится нормально.

## Решение для B5

1. **Оставляем `format="markdown"`** в `MarkdownSender.send()` — это правильный API-вызов.
2. **Добавляем plain-text skill** в `app/llm/skills/markdown_format.md` — на случай если MAX опять не отрисует, LLM должен уметь выдавать plain-text с эмодзи-структурой.
3. **В промптах (Cappadonna)** — добавляем явное правило: "Если Markdown не работает в клиенте — используй эмодзи-маркеры ▶ ✅ ⚠️ 💡 и ЗАГЛАВНЫЕ БУКВЫ для заголовков".
5. **MESSAGE_FORMAT=plain в .env** остаётся валидным fallback — отключает format=markdown вообще.

## Минимальный smoke-curl для проверки вживую

```bash
curl -X POST "https://platform-api2.max.ru/messages" \
  -H "Authorization: $MAX_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": 154939916, "text": "**R1-CURL-TEST**", "format": "markdown"}'
```

## Что осталось проверить руками Pavel'я

Открыть чат с ботом и посмотреть на 3 сообщения `R1-TEST-1/2/3`:
- `R1-TEST-1` (markdown) — должно быть жирным/курсивом/ссылкой
- `R1-TEST-2` (html) — тоже должно отрисуться
- `R1-TEST-3` (plain) — обычный текст

Если `R1-TEST-1` отрисовался с `**жирный**` литералами — значит **MAX API НЕ рендерит markdown вообще** (тогда Pavel'у надо жаловаться в поддержку MAX, а наша задача — plain-text skill).

Если отрисовался нормально — баг был только в inline-callback (особенность MAX UI).