import os

import pytest

from app.db.storage import Storage


@pytest.mark.asyncio
async def test_known_channel_is_listed_after_bot_added(tmp_path):
    storage = Storage(os.path.join(tmp_path, "bot.db"))
    await storage.init()
    try:
        await storage.upsert_known_chat(
            chat_id=-1001,
            title="Канал эксперта",
            is_channel=True,
            active=True,
        )
        rows = await storage.list_known_channels()
        assert [(row.chat_id, row.title) for row in rows] == [
            (-1001, "Канал эксперта")
        ]
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_removed_channel_is_not_listed(tmp_path):
    storage = Storage(os.path.join(tmp_path, "bot.db"))
    await storage.init()
    try:
        await storage.upsert_known_chat(-1001, "Канал", True, True)
        await storage.set_known_chat_active(-1001, False)
        assert await storage.list_known_channels() == []
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_group_is_not_returned_as_channel(tmp_path):
    storage = Storage(os.path.join(tmp_path, "bot.db"))
    await storage.init()
    try:
        await storage.upsert_known_chat(-2001, "Группа", False, True)
        assert await storage.list_known_channels() == []
    finally:
        await storage.close()


@pytest.mark.asyncio
async def test_known_channel_title_is_updated(tmp_path):
    storage = Storage(os.path.join(tmp_path, "bot.db"))
    await storage.init()
    try:
        await storage.upsert_known_chat(-1001, "Старое", True, True)
        await storage.upsert_known_chat(-1001, "Новое", True, True)
        rows = await storage.list_known_channels()
        assert len(rows) == 1
        assert rows[0].title == "Новое"
    finally:
        await storage.close()
