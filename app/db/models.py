"""SQLite data models (plain dataclasses; persistence handled by storage.py)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class User:
    user_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_admin: bool = False
    created_at: Optional[str] = None


@dataclass
class Message:
    id: Optional[int] = None
    chat_id: Optional[int] = None
    user_id: Optional[int] = None
    role: str = "user"  # user | assistant
    text: str = ""
    created_at: Optional[str] = None


@dataclass
class Publication:
    id: Optional[int] = None
    chat_id: int = 0
    channel: str = ""
    text: str = ""
    status: str = "pending"  # pending | published | rejected | edited
    preview_message_id: Optional[str] = None
    published_message_id: Optional[str] = None
    created_at: Optional[str] = None
    decided_at: Optional[str] = None


@dataclass
class Session:
    """A conversation session keyed by (chat_id, user_id)."""

    id: Optional[int] = None
    chat_id: Optional[int] = None
    user_id: Optional[int] = None
    context_json: str = "[]"
    updated_at: Optional[str] = None


@dataclass
class GeneratedImage:
    """One image produced by the image_generation API.

    ``image_path`` points at a local file inside ``settings.image_storage_dir``
    (we download the URL right away — the remote URL expires in 24h).
    ``preview_message_id`` is the bot message that first showed this image;
    ``attached_to_publication_id`` is the post that used this image as cover
    (set when the user presses [📤 В канал]).
    """

    id: Optional[int] = None
    user_id: int = 0
    post_text: str = ""
    prompt: str = ""
    aspect_ratio: str = "1:1"
    image_path: str = ""
    preview_message_id: Optional[str] = None
    attached_to_publication_id: Optional[int] = None
    created_at: Optional[str] = None


@dataclass
class KnownChat:
    """Group/channel discovered from MAX bot_added lifecycle events."""

    chat_id: int
    title: str = ""
    is_channel: bool = False
    active: bool = True
    first_seen_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class HermesSession:
    """One Hermes task run from the MAX bot (Feature V3, 2026-08-19).

    Lifecycle: created with status='running', then either 'done', 'failed'
    or 'timeout' when the session is finished. ``progress_json`` is a JSON
    array of short lines we periodically post back to the user.
    """

    id: Optional[int] = None
    user_id: int = 0
    chat_id: int = 0
    role: str = "chat"
    task: str = ""
    scenario: str = "custom"
    status: str = "running"
    progress_json: str = "[]"
    result_text: Optional[str] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
