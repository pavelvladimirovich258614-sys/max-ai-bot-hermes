"""Application configuration loaded from environment / .env via pydantic-settings.

Pavel fills in real secrets in a local .env file (never committed). Nothing
here is a real secret — every value has a safe no-op default so the app can
import and run tests without credentials.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- MAX Bot ----
    max_bot_token: str = ""
    # HARD RULE: only the current, non-deprecated domain.
    max_api_base: str = "https://platform-api2.max.ru"
    max_webhook_url: str = ""
    max_use_polling: bool = True
    max_admin_user_ids: str = ""  # comma-separated Telegram-style numeric ids

    # ---- Hermes (RZA) ----
    hermes_mode: Literal["auto", "http", "cli", "none"] = "auto"
    hermes_rza_url: str = "http://host.docker.internal:9119/api/hermes/route"
    hermes_rza_cli: str = "hermes peer dm rza"

    # ---- LLM (primary) ----
    llm_provider: str = "minimax"
    llm_api_key: str = ""
    # Alias kept for Pavel's original .env naming (LLM_PRIMARY_API_KEY from the spec).
    # Takes priority over llm_api_key if both are set.
    llm_primary_api_key: str = ""
    llm_base_url: str = "https://api.minimax.io/anthropic"
    llm_model: str = "MiniMax-M3"
    # Mode for the primary provider: "anthropic" (Messages API) or "openai" (chat/completions)
    llm_primary_style: str = "anthropic"

    # ---- LLM (fallback) ----
    llm_fallback_provider: str = "stepfun"
    llm_fallback_api_key: str = ""
    llm_fallback_base_url: str = "https://api.stepfun.ai/v1"
    llm_fallback_model: str = "step-3.7-flash"
    llm_fallback_style: str = "openai"

    # ---- Web search ----
    # duckduckgo | searxng | whoogle | librex
    web_search_backend: str = "duckduckgo"
    searxng_url: str = ""
    whoogle_url: str = ""
    librex_url: str = ""
    searx_space_url: str = "https://searx.space"

    # ---- Parsing ----
    scrapling_enabled: bool = True
    crawlee_enabled: bool = False

    # ---- App ----
    log_level: str = "INFO"
    # SQLite file location (aiosqlite). Volume-mounted in Docker.
    database_url: str = "sqlite+aiosqlite:///./data/bot.db"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    # Inbound webhook path that MAX POSTs updates to.
    webhook_path: str = "/webhook/max"

    # ---- Markdown formatting ----
    # "markdown" (default) | "html" | "plain". Set to "plain" to revert to the
    # old plain-text-with-emoji behaviour without code changes.
    message_format: str = "markdown"

    # ---- Image generation (MiniMax) ----
    image_aspect_default: str = "1:1"
    # Directory where generated PNG bytes are stored; relative paths are anchored
    # to CWD at startup. Mounted as a volume in Docker.
    image_storage_dir: str = "./data/images"
    # B4 (2026-08-19): MiniMax image-01 бывает медленным — увеличил до 120с.
    image_request_timeout_s: float = 120.0
    # Скачивание готовой картинки по URL — обычно быстро (CDN).
    image_download_timeout_s: float = 60.0
    # Max prompt length for image-01 (Pavel: 1500 chars; we truncate, never error).
    image_prompt_max_chars: int = 1500
    # Retry budget for image_generation (network blips + 1002 rate limit).
    image_max_retries: int = 2

    @property
    def admin_user_ids(self) -> list[int]:
        out: list[int] = []
        for part in self.max_admin_user_ids.split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out

    @property
    def db_path(self) -> str:
        """Extract the sqlite file path from the DATABASE_URL."""
        url = self.database_url
        if url.startswith("sqlite+aiosqlite:///"):
            return url[len("sqlite+aiosqlite:///"):]
        if url.startswith("sqlite+aiosqlite://"):
            return url[len("sqlite+aiosqlite://"):]
        return "./data/bot.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
