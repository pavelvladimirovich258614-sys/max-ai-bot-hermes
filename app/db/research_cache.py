"""SQLite-кэш для research cascade.

Async-first поверх aiosqlite. TTL 1 час по умолчанию (настраивается).

Использование как decorator (НЕ интегрирован в research_cascade —
это отдельный модуль для будущих задач Batch 3):

    from app.db.research_cache import ResearchCache, cache

    cache_instance = ResearchCache(db_path=settings.database_path)

    @cache(ttl_seconds=3600)
    async def run_research(topic: str, freshness: str, params: dict):
        # дорогостоящая логика cascade
        return {"topic": topic, "facts": [...]}

    result = await run_research("AI avatars", "week", {"limit": 5})

Прямое использование:

    rc = ResearchCache(db_path="/path/db.sqlite")
    await rc.init()
    key = ResearchCache.make_key("AI", "week", {"limit": 5})
    await rc.set(key, {"facts": [1, 2, 3]})
    cached = await rc.get(key)         # {"facts": [1, 2, 3]}
    await rc.invalidate(key)
    deleted = await rc.cleanup_expired()
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

import aiosqlite


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


class ResearchCache:
    """Async SQLite-кэш для research cascade.

    Кэш живёт в той же БД, что и остальное (Storage.init() создаёт
    таблицу `research_cache` через CREATE TABLE IF NOT EXISTS, см.
    `app/db/storage.py`). Мы не открываем отдельный файл — мы
    ходим в уже-инициализированную БД.

    Тем не менее, конструктор принимает `db_path` и открывает свой
    собственный `aiosqlite.Connection`, чтобы не зависеть от
    lifecycle основного Storage. Это удобно для тестов и для
    самостоятельного вызова из cascade.
    """

    def __init__(self, db_path: str, ttl_seconds: int = 3600) -> None:
        self._db_path = db_path
        self._ttl_seconds = int(ttl_seconds)
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """Открыть соединение и убедиться, что таблица существует.

        Миграция (CREATE TABLE IF NOT EXISTS) уже выполняется в
        `Storage._create_tables`. Здесь мы создаём таблицу, если
        этот модуль используется автономно (например, в тестах с
        временной БД), через безопасный IF NOT EXISTS.
        """
        parent = os.path.dirname(self._db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_cache (
                cache_key TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                hit_count INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_research_cache_expires
                ON research_cache(expires_at);
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ---------- key helpers ----------

    @staticmethod
    def make_key(topic: str, freshness: str, params: dict) -> str:
        """sha256(topic + ":" + freshness + ":" + json.dumps(params, sort_keys=True))."""
        payload = (
            str(topic)
            + ":"
            + str(freshness)
            + ":"
            + json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def make_key_from_args(func_name: str, args: tuple, kwargs: dict) -> str:
        """Ключ из args/kwargs вызываемой функции.

        Эвристика: первый позиционный аргумент = topic,
        второй (если есть) = freshness, остальное = params.
        Если в функцию переданы kwargs с именами `topic`/`freshness`/`params`,
        используем их напрямую.
        """
        topic = kwargs.get("topic")
        freshness = kwargs.get("freshness")
        params = kwargs.get("params")

        if topic is None or freshness is None:
            # возьмём позиционные
            if len(args) >= 1 and topic is None:
                topic = args[0]
            if len(args) >= 2 and freshness is None:
                freshness = args[1]
            if len(args) >= 3 and params is None:
                params = args[2]

        params = params if isinstance(params, dict) else {}
        # Имя функции подмешиваем, чтобы @cache на разных функциях
        # не давал коллизий при одинаковых topic/freshness/params.
        full_params = dict(params)
        full_params["__func__"] = func_name
        return ResearchCache.make_key(str(topic), str(freshness), full_params)

    # ---------- CRUD ----------

    async def get(self, key: str) -> Optional[dict]:
        """Вернуть запись или None, если истекла / отсутствует.

        При попадании (не expired) — увеличиваем hit_count.
        При истечении — удаляем запись, возвращаем None.
        """
        assert self._conn is not None, "ResearchCache.init() не вызван"
        cur = await self._conn.execute(
            "SELECT result_json, expires_at FROM research_cache WHERE cache_key = ?",
            (key,),
        )
        row = await cur.fetchone()
        if row is None:
            return None

        expires_at = _parse_iso(row["expires_at"])
        now = _now()
        if expires_at <= now:
            # истёк — выкидываем
            await self._conn.execute(
                "DELETE FROM research_cache WHERE cache_key = ?", (key,)
            )
            await self._conn.commit()
            return None

        # cache hit — инкрементируем счётчик
        await self._conn.execute(
            "UPDATE research_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
            (key,),
        )
        await self._conn.commit()
        return json.loads(row["result_json"])

    async def set(self, key: str, result: dict) -> None:
        """Сохранить результат с TTL."""
        assert self._conn is not None, "ResearchCache.init() не вызван"
        now = _now()
        expires = now + timedelta(seconds=self._ttl_seconds)
        await self._conn.execute(
            """
            INSERT INTO research_cache (cache_key, result_json, created_at, expires_at, hit_count)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(cache_key) DO UPDATE SET
                result_json=excluded.result_json,
                created_at=excluded.created_at,
                expires_at=excluded.expires_at,
                hit_count=0
            """,
            (key, json.dumps(result, ensure_ascii=False, default=str),
             _iso(now), _iso(expires)),
        )
        await self._conn.commit()

    async def invalidate(self, key: str) -> None:
        """Удалить одну запись по ключу. Нет ошибки, если её не было."""
        assert self._conn is not None, "ResearchCache.init() не вызван"
        await self._conn.execute(
            "DELETE FROM research_cache WHERE cache_key = ?", (key,)
        )
        await self._conn.commit()

    async def cleanup_expired(self) -> int:
        """Удалить все истёкшие записи, вернуть количество удалённых."""
        assert self._conn is not None, "ResearchCache.init() не вызван"
        cur = await self._conn.execute(
            "DELETE FROM research_cache WHERE expires_at <= ?",
            (_iso(_now()),),
        )
        await self._conn.commit()
        return cur.rowcount or 0

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds


# ---------------- decorator ----------------

# Глобальный instance, который будет лениво инициализирован при первом
# вызове обёрнутой функции. Тесты могут переопределить через `cache.configure()`.
_default_cache: Optional[ResearchCache] = None
_default_db_path: Optional[str] = None


def configure_default_cache(db_path: str, ttl_seconds: int = 3600) -> ResearchCache:
    """Установить путь к БД для глобального кэша, используемого @cache.

    Без вызова этой функции @cache упадёт с понятной ошибкой.
    """
    global _default_cache, _default_db_path
    _default_db_path = db_path
    _default_cache = ResearchCache(db_path=db_path, ttl_seconds=ttl_seconds)
    return _default_cache


async def _get_default_cache() -> ResearchCache:
    global _default_cache
    if _default_cache is None:
        raise RuntimeError(
            "ResearchCache не инициализирован. "
            "Вызовите configure_default_cache(db_path=...) перед использованием @cache."
        )
    if _default_cache._conn is None:
        await _default_cache.init()
    return _default_cache


def cache(
    ttl_seconds: int = 3600,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator: кэширует результат async-функции в research_cache.

    Ключ считается из имени функции и её аргументов:
      - если в kwargs есть topic/freshness/params — берутся они;
      - иначе берутся первые 1-3 позиционных аргумента.

    Decorator НЕ лезет в cascade сам — это просто обёртка над
    ResearchCache.set/get. Возвращаемое значение функции должно
    быть JSON-сериализуемым (dict, list, str, int, float, bool, None).
    """
    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            cache_instance = await _get_default_cache()
            key = ResearchCache.make_key_from_args(func.__name__, args, kwargs)
            cached = await cache_instance.get(key)
            if cached is not None:
                return cached
            result = await func(*args, **kwargs)
            # Если функция вернула None — не кэшируем (None-семантика "miss").
            if result is not None:
                try:
                    await cache_instance.set(key, result)
                except (TypeError, ValueError):
                    # не сериализуется — просто не кладём в кэш
                    pass
            return result

        wrapper.__wrapped__ = func  # type: ignore[attr-defined]
        return wrapper

    return decorator
