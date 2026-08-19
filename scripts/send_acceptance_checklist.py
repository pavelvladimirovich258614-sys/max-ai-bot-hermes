import asyncio
import ssl
from pathlib import Path

import httpx

from app.config import get_settings


CHAT_ID = 154939916
TEXT = """🧪 РУЧНАЯ ПРОВЕРКА REFACTOR V3

Пожалуйста, проверь по порядку:

1. Нажми 🎨 Картинка → Свой промпт → выбери пропорции → введи описание. Должно появиться изображение без ошибки FSM.
2. Нажми 📤 Пост → Мои каналы. Каналы появятся после того, как бот получит событие добавления в канал; если список пуст, будет понятная подсказка.
3. Нажми 📤 Пост → Ввести chat_id вручную. Бот должен ждать следующее сообщение, а не возвращать в меню. Кнопка ❌ Отмена должна сбросить ожидание.
4. Нажми 🔍 Исследование или ✍️ Копирайтинг. В ответе не должно быть литералов двойных звёздочек, решёток и блоков кода.
5. Введи /. MAX должен показать 11 команд.
6. Нажми 🤖 Hermes и по очереди проверь: Контент-план, Ресёрч, Произвольная задача.
7. Нажми несколько разных inline-кнопок. Сверху не должны появляться пустые белые блоки или отдельные сообщения с песочными часами.

Если пункт не проходит — пришли номер пункта и скриншот. До этого релиз остаётся в статусе «ожидает ручной проверки»."""


async def main() -> None:
    settings = get_settings()
    headers = {
        "Authorization": settings.max_bot_token,
        "Content-Type": "application/json",
    }
    root_ca = Path(__file__).resolve().parents[1] / "certs" / "russian_trusted_root_ca_pem.crt"
    ssl_context = ssl.create_default_context()
    ssl_context.load_verify_locations(cafile=root_ca)
    async with httpx.AsyncClient(timeout=30.0, verify=ssl_context) as client:
        response = await client.post(
            f"{settings.max_api_base.rstrip('/')}/messages",
            params={"chat_id": CHAT_ID},
            headers=headers,
            json={"text": TEXT},
        )
    print(f"acceptance_message status={response.status_code}")
    response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
