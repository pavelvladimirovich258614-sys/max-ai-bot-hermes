"""Tests for app/db/research_cache.py — SQLite-кэш с TTL 1h.

Стиль соответствует test_orchestrator_fallback.py: async-логика
запускается через `asyncio.run(...)` синхронно, без pytest-asyncio.

Покрывает:
  - roundtrip get/set
  - возврат None для expired
  - инкремент hit_count на попадании
  - детерминированность make_key
  - cleanup_expired удаляет старые
  - @cache decorator кэширует результат функции
  - @cache decorator прокидывает cache miss → вызов
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time

import aiosqlite

from app.db import research_cache as rc_module
from app.db.research_cache import ResearchCache, cache, configure_default_cache


# ---------------- helpers ----------------


def _run(coro):
    return asyncio.run(coro)


def _temp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    return path


# ---------------- tests ----------------


def test_cache_set_get_roundtrip() -> None:
    """set → get возвращает тот же dict."""
    path = _temp_db()

    async def scenario():
        cache_obj = ResearchCache(db_path=path, ttl_seconds=60)
        await cache_obj.init()
        try:
            payload = {"facts": [{"title": "A1", "url": "https://ex.com"}], "summary": "ok"}
            key = ResearchCache.make_key("AI avatars", "week", {"limit": 5})
            assert await cache_obj.get(key) is None  # пусто
            await cache_obj.set(key, payload)
            got = await cache_obj.get(key)
            assert got == payload
        finally:
            await cache_obj.close()

    try:
        _run(scenario())
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_cache_returns_none_for_expired() -> None:
    """TTL=1s → через задержку get возвращает None и удаляет запись."""
    path = _temp_db()

    async def scenario():
        cache_obj = ResearchCache(db_path=path, ttl_seconds=1)
        await cache_obj.init()
        try:
            key = ResearchCache.make_key("x", "day", {})
            await cache_obj.set(key, {"v": 1})
            assert await cache_obj.get(key) == {"v": 1}
            time.sleep(1.5)
            assert await cache_obj.get(key) is None
            # expired запись удалена
            async with aiosqlite.connect(path) as conn:
                cur = await conn.execute(
                    "SELECT COUNT(*) AS c FROM research_cache WHERE cache_key = ?",
                    (key,),
                )
                row = await cur.fetchone()
                assert row[0] == 0
        finally:
            await cache_obj.close()

    try:
        _run(scenario())
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_cache_hit_increments_counter() -> None:
    """get на свежей записи инкрементирует hit_count."""
    path = _temp_db()

    async def scenario():
        cache_obj = ResearchCache(db_path=path, ttl_seconds=60)
        await cache_obj.init()
        try:
            key = ResearchCache.make_key("topic", "month", {"k": 1})
            await cache_obj.set(key, {"data": 42})
            assert await cache_obj.get(key) == {"data": 42}
            assert await cache_obj.get(key) == {"data": 42}
            assert await cache_obj.get(key) == {"data": 42}

            async with aiosqlite.connect(path) as conn:
                cur = await conn.execute(
                    "SELECT hit_count FROM research_cache WHERE cache_key = ?", (key,)
                )
                row = await cur.fetchone()
                assert row is not None
                assert row[0] == 3
        finally:
            await cache_obj.close()

    try:
        _run(scenario())
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_cache_make_key_deterministic() -> None:
    """Одинаковые аргументы → одинаковый ключ, разные порядки ключей params → одинаковый ключ."""
    a = ResearchCache.make_key("ai", "week", {"limit": 5, "lang": "ru"})
    b = ResearchCache.make_key("ai", "week", {"lang": "ru", "limit": 5})
    assert a == b
    assert len(a) == 64  # sha256 hex

    c = ResearchCache.make_key("ai", "week", {"limit": 6})
    assert a != c

    d = ResearchCache.make_key("ai", "month", {"limit": 5, "lang": "ru"})
    assert a != d

    e = ResearchCache.make_key("ai2", "week", {"limit": 5, "lang": "ru"})
    assert a != e


def test_cache_cleanup_expired_removes_old() -> None:
    """cleanup_expired удаляет истёкшие записи, оставляет свежие."""
    path = _temp_db()
    from datetime import datetime, timedelta, timezone

    async def scenario():
        cache_obj = ResearchCache(db_path=path, ttl_seconds=1)
        await cache_obj.init()
        try:
            expired_key = ResearchCache.make_key("old", "day", {})
            fresh_key = ResearchCache.make_key("new", "day", {})
            await cache_obj.set(expired_key, {"x": 1})
            await cache_obj.set(fresh_key, {"x": 2})

            time.sleep(1.2)
            # Прямо вставим свежую запись с expires_at в будущем —
            # обходной путь, чтобы получить смешанное состояние в БД.
            async with aiosqlite.connect(path) as conn:
                future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
                now = datetime.now(timezone.utc).isoformat()
                await conn.execute(
                    """INSERT OR REPLACE INTO research_cache
                       (cache_key, result_json, created_at, expires_at, hit_count)
                       VALUES (?, ?, ?, ?, 0)""",
                    (fresh_key, '{"x": 2}', now, future),
                )
                await conn.commit()

            deleted = await cache_obj.cleanup_expired()
            assert deleted == 1  # только expired_key удалён

            # fresh_key остался
            assert await cache_obj.get(fresh_key) == {"x": 2}
        finally:
            await cache_obj.close()

    try:
        _run(scenario())
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_decorator_caches_function_result() -> None:
    """@cache сохраняет результат функции и не вызывает её повторно."""
    path = _temp_db()
    rc_module._default_cache = None
    rc_module._default_db_path = None
    configure_default_cache(db_path=path, ttl_seconds=60)

    call_count = {"n": 0}

    @cache(ttl_seconds=60)
    async def fake_research(topic: str, freshness: str, params: dict) -> dict:
        call_count["n"] += 1
        return {"topic": topic, "facts": [1, 2, 3], "call": call_count["n"]}

    async def scenario():
        r1 = await fake_research("AI", "week", {"limit": 5})
        r2 = await fake_research("AI", "week", {"limit": 5})
        r3 = await fake_research("AI", "week", {"limit": 5})
        assert r1 == {"topic": "AI", "facts": [1, 2, 3], "call": 1}
        assert r2 == {"topic": "AI", "facts": [1, 2, 3], "call": 1}
        assert r3 == {"topic": "AI", "facts": [1, 2, 3], "call": 1}
        assert call_count["n"] == 1
        # Другой набор params → промах → новый вызов
        r4 = await fake_research("AI", "week", {"limit": 6})
        assert r4 == {"topic": "AI", "facts": [1, 2, 3], "call": 2}
        assert call_count["n"] == 2

    try:
        _run(scenario())
    finally:
        if rc_module._default_cache is not None:
            _run(rc_module._default_cache.close())
        rc_module._default_cache = None
        rc_module._default_db_path = None
        if os.path.exists(path):
            os.remove(path)


def test_decorator_skips_on_cache_miss() -> None:
    """Промах кэша → функция вызвана, результат закэширован; второй вызов — hit."""
    path = _temp_db()
    rc_module._default_cache = None
    rc_module._default_db_path = None
    configure_default_cache(db_path=path, ttl_seconds=60)

    seen_args: list[tuple[str, str, dict]] = []

    @cache(ttl_seconds=60)
    async def gather(topic: str, freshness: str, params: dict) -> dict:
        seen_args.append((topic, freshness, dict(params)))
        return {"echo": f"{topic}:{freshness}:{params.get('n', 0)}"}

    async def scenario():
        # Первый вызов — miss
        r1 = await gather("X", "day", {"n": 1})
        assert r1 == {"echo": "X:day:1"}
        assert seen_args == [("X", "day", {"n": 1})]

        # Второй вызов с теми же args — hit, функция НЕ вызвана
        r2 = await gather("X", "day", {"n": 1})
        assert r2 == {"echo": "X:day:1"}
        assert seen_args == [("X", "day", {"n": 1})]

        # Прямая проверка через БД: запись существует
        async with aiosqlite.connect(path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM research_cache")
            row = await cur.fetchone()
            assert row[0] == 1

    try:
        _run(scenario())
    finally:
        if rc_module._default_cache is not None:
            _run(rc_module._default_cache.close())
        rc_module._default_cache = None
        rc_module._default_db_path = None
        if os.path.exists(path):
            os.remove(path)
