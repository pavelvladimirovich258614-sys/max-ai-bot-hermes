"""SQLite CRUD via aiosqlite. Async, no sync usage anywhere.

All access goes through this module; the rest of the app never imports
`aiosqlite` directly.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from app.db import models


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        parent = os.path.dirname(self._db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _create_tables(self) -> None:
        assert self._conn is not None
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                is_admin INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                role TEXT,
                text TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                channel TEXT,
                text TEXT,
                status TEXT DEFAULT 'pending',
                preview_message_id TEXT,
                published_message_id TEXT,
                created_at TEXT,
                decided_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                context_json TEXT DEFAULT '[]',
                updated_at TEXT,
                UNIQUE(chat_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS antispam_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                text_hash TEXT,
                raw_text TEXT,
                msg_id TEXT,
                created_at TEXT,
                ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS antispam_bans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                banned_until TEXT,      -- ISO; NULL = permanent
                banned_until_ts INTEGER, -- epoch seconds; NULL = permanent
                reason TEXT,
                created_at TEXT,
                ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS generated_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                post_text TEXT,
                prompt TEXT,
                aspect_ratio TEXT DEFAULT '1:1',
                image_path TEXT,
                preview_message_id TEXT,
                attached_to_publication_id INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS known_chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT DEFAULT '',
                is_channel INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                first_seen_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS hermes_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                role TEXT,
                task TEXT,
                scenario TEXT,           -- 'plan' | 'research' | 'custom'
                status TEXT DEFAULT 'running',  -- running | done | failed | timeout
                progress_json TEXT DEFAULT '[]',-- list[str] of progress lines
                result_text TEXT,
                created_at TEXT,
                finished_at TEXT
            );
            """
        )

    # ---- users ----
    async def upsert_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        is_admin: bool = False,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name, is_admin, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                is_admin=excluded.is_admin
            """,
            (user_id, username, first_name, last_name, int(is_admin), _now()),
        )
        await self._conn.commit()

    async def get_user(self, user_id: int) -> Optional[models.User]:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["is_admin"] = bool(d.get("is_admin"))
        return models.User(**d)

    # ---- messages ----
    async def add_message(
        self,
        chat_id: Optional[int],
        user_id: Optional[int],
        role: str,
        text: str,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "INSERT INTO messages (chat_id, user_id, role, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, user_id, role, text, _now()),
        )
        await self._conn.commit()

    async def recent_messages(
        self, chat_id: Optional[int], user_id: Optional[int], limit: int = 10
    ) -> list[models.Message]:
        assert self._conn is not None
        cur = await self._conn.execute(
            """
            SELECT * FROM messages
            WHERE (chat_id = ? OR (chat_id IS NULL AND ? IS NULL))
              AND (user_id = ? OR (user_id IS NULL AND ? IS NULL))
            ORDER BY id DESC LIMIT ?
            """,
            (chat_id, chat_id, user_id, user_id, limit),
        )
        rows = await cur.fetchall()
        return [models.Message(**dict(r)) for r in reversed(rows)]

    # ---- generated_images (image-01 cache) ----
    async def create_generated_image(
        self,
        user_id: int,
        post_text: str,
        prompt: str,
        aspect_ratio: str,
        image_path: str,
        preview_message_id: Optional[str] = None,
    ) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(
            """INSERT INTO generated_images
               (user_id, post_text, prompt, aspect_ratio, image_path,
                preview_message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, post_text, prompt, aspect_ratio, image_path,
             preview_message_id, _now()),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_generated_image(self, image_id: int) -> Optional[models.GeneratedImage]:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT * FROM generated_images WHERE id = ?", (image_id,)
        )
        row = await cur.fetchone()
        return models.GeneratedImage(**dict(row)) if row else None

    async def update_generated_image_preview(
        self, image_id: int, preview_message_id: str
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE generated_images SET preview_message_id = ? WHERE id = ?",
            (preview_message_id, image_id),
        )
        await self._conn.commit()

    async def update_generated_image_attachment(
        self, image_id: int, publication_id: int
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE generated_images SET attached_to_publication_id = ? WHERE id = ?",
            (publication_id, image_id),
        )
        await self._conn.commit()

    async def update_generated_image_path(
        self, image_id: int, image_path: str
    ) -> None:
        """Persist the actual on-disk path of the saved PNG.

        Called by image_gen._save_image() after writing bytes to disk —
        we use this instead of poking at `self._conn` from outside the
        Storage class (Audit HIGH #4, 2026-08-19).
        """
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE generated_images SET image_path = ? WHERE id = ?",
            (image_path, image_id),
        )
        await self._conn.commit()

    # ---- hermes_sessions (Feature V3) ----
    async def create_hermes_session(
        self,
        user_id: int,
        chat_id: int,
        role: str,
        task: str,
        scenario: str,
    ) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(
            """INSERT INTO hermes_sessions
               (user_id, chat_id, role, task, scenario, status,
                progress_json, created_at)
               VALUES (?, ?, ?, ?, ?, 'running', '[]', ?)""",
            (user_id, chat_id, role, task, scenario, _now()),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def update_hermes_session_progress(
        self, session_id: int, progress: list[str]
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE hermes_sessions SET progress_json = ? WHERE id = ?",
            (json.dumps(progress, ensure_ascii=False), session_id),
        )
        await self._conn.commit()

    async def finish_hermes_session(
        self,
        session_id: int,
        *,
        status: str,
        result_text: str | None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """UPDATE hermes_sessions
               SET status = ?, result_text = ?, finished_at = ?
               WHERE id = ?""",
            (status, result_text, _now(), session_id),
        )
        await self._conn.commit()

    async def get_hermes_session(self, session_id: int) -> Optional["models.HermesSession"]:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT * FROM hermes_sessions WHERE id = ?", (session_id,)
        )
        row = await cur.fetchone()
        return models.HermesSession(**dict(row)) if row else None

    # ---- known_chats (MAX removed GET /chats in June 2026) ----
    async def upsert_known_chat(
        self,
        chat_id: int,
        title: str = "",
        is_channel: bool = False,
        active: bool = True,
    ) -> None:
        assert self._conn is not None
        now = _now()
        await self._conn.execute(
            """INSERT INTO known_chats
               (chat_id, title, is_channel, active, first_seen_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   title=excluded.title,
                   is_channel=excluded.is_channel,
                   active=excluded.active,
                   updated_at=excluded.updated_at""",
            (chat_id, title or "", int(is_channel), int(active), now, now),
        )
        await self._conn.commit()

    async def set_known_chat_active(self, chat_id: int, active: bool) -> None:
        assert self._conn is not None
        await self._conn.execute(
            "UPDATE known_chats SET active = ?, updated_at = ? WHERE chat_id = ?",
            (int(active), _now(), chat_id),
        )
        await self._conn.commit()

    async def list_known_channels(self) -> list[models.KnownChat]:
        assert self._conn is not None
        cur = await self._conn.execute(
            """SELECT * FROM known_chats
               WHERE active = 1 AND is_channel = 1
               ORDER BY title COLLATE NOCASE, chat_id"""
        )
        rows = await cur.fetchall()
        result: list[models.KnownChat] = []
        for row in rows:
            data = dict(row)
            data["is_channel"] = bool(data["is_channel"])
            data["active"] = bool(data["active"])
            result.append(models.KnownChat(**data))
        return result

    # ---- sessions (free-chat context) ----
    async def get_session_context(
        self, chat_id: Optional[int], user_id: Optional[int], limit: int = 10
    ) -> list[dict]:
        """Return the last `limit` {role, content} pairs for a chat/user pair."""
        msgs = await self.recent_messages(chat_id, user_id, limit=limit)
        return [{"role": m.role, "content": m.text} for m in msgs]

    # ---- publications ----
    async def create_publication(
        self,
        chat_id: int,
        channel: str,
        text: str,
        preview_message_id: Optional[str] = None,
    ) -> int:
        assert self._conn is not None
        cur = await self._conn.execute(
            """
            INSERT INTO publications (chat_id, channel, text, status, preview_message_id, created_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (chat_id, channel, text, preview_message_id, _now()),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_publication(self, pub_id: int) -> Optional[models.Publication]:
        assert self._conn is not None
        cur = await self._conn.execute(
            "SELECT * FROM publications WHERE id = ?", (pub_id,)
        )
        row = await cur.fetchone()
        return models.Publication(**dict(row)) if row else None

    async def update_publication(
        self,
        pub_id: int,
        status: str,
        published_message_id: Optional[str] = None,
    ) -> None:
        assert self._conn is not None
        await self._conn.execute(
            """
            UPDATE publications
            SET status = ?, published_message_id = ?, decided_at = ?
            WHERE id = ?
            """,
            (status, published_message_id, _now(), pub_id),
        )
        await self._conn.commit()
