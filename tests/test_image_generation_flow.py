from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.max.handlers import image_gen


class _Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edits: list[tuple[str, str]] = []
        self._counter = 0

    async def send_message(self, chat_id, text, **_kwargs):
        self._counter += 1
        self.sent.append(text or "")
        return SimpleNamespace(
            message=SimpleNamespace(body=SimpleNamespace(mid=f"progress-{self._counter}"))
        )

    async def edit_message(self, mid, text, **_kwargs):
        self.edits.append((mid, text))


@pytest.mark.asyncio
async def test_post_to_image_starts_visible_progress_before_generation():
    bot = _Bot()
    deps = SimpleNamespace(storage=AsyncMock())
    preview = AsyncMock()

    with (
        patch(
            "app.max.handlers.image_gen._safe_orchestrator_run",
            new=AsyncMock(return_value="wide editorial visual prompt"),
        ),
        patch(
            "app.max.handlers.image_gen._generate_bytes",
            new=AsyncMock(return_value=b"image-bytes"),
        ) as generate,
        patch(
            "app.max.handlers.image_gen._save_image",
            new=AsyncMock(return_value=(7, "data/images/7.jpg")),
        ),
        patch(
            "app.max.handlers.image_gen._send_preview",
            new=preview,
        ),
    ):
        await image_gen._generate_from_post(
            deps,
            bot,
            chat_id=154939916,
            user_id=73412011,
            post_text="Длинный пост об ИИ-агентах",
            aspect="16:9",
        )

    assert bot.sent and "Превращаю пост в промпт" in bot.sent[0]
    generate.assert_awaited_once_with(
        image_gen.get_settings(), "wide editorial visual prompt", "16:9"
    )
    preview.assert_awaited_once()


def test_explicit_format_line_overrides_wide_default_for_post_image():
    assert image_gen._aspect_from_post_text("Пост\nФормат: 9:16", "16:9") == "9:16"
    assert image_gen._aspect_from_post_text("Пост\nПожелания: тёплый свет", "16:9") == "16:9"
