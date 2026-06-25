"""
config.py
---------
Centralised configuration loader.
All values are pulled exclusively from environment variables (Koyeb secrets /
.env file). No hardcoded credentials anywhere in the codebase.

Usage:
    from config import cfg
    print(cfg.API_ID, cfg.TARGET_CHANNEL_ID)
"""

import sys
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """
    Pydantic-Settings automatically reads each field from the matching
    environment variable name (case-insensitive).  An .env file in the
    project root is also picked up automatically when present locally.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Telegram App credentials (create at https://my.telegram.org) ──────────
    API_ID: int = Field(..., description="Telegram API ID (integer)")
    API_HASH: str = Field(..., description="Telegram API Hash (hex string)")

    # ── Master Bot token from @BotFather ──────────────────────────────────────
    BOT_TOKEN: str = Field(..., description="Telegram Bot token")

    # ── MongoDB Atlas connection string ───────────────────────────────────────
    MONGO_URI: str = Field(..., description="MongoDB connection URI")

    # ── Destination private channel (must use -100xxxxxxxxxx format) ──────────
    TARGET_CHANNEL_ID: int = Field(
        ..., description="Target channel ID with -100 prefix"
    )

    # ── HTTP health-check port assigned by Koyeb (default 8080) ──────────────
    PORT: int = Field(default=8080, description="HTTP health-check port")

    # ── MongoDB database / collection names (override if needed) ──────────────
    MONGO_DB_NAME: str = Field(default="tg_forwarder", description="MongoDB database")
    MONGO_COLLECTION: str = Field(
        default="sessions", description="MongoDB collection for session storage"
    )

    # ── Pyrogram session name used for the userbot worker file ────────────────
    WORKER_SESSION_NAME: str = Field(
        default="userbot_worker", description="Pyrogram session name"
    )


# ── Singleton instance – import this everywhere ───────────────────────────────
try:
    cfg = Settings()  # type: ignore[call-arg]
except Exception as exc:
    print(
        f"[FATAL] Failed to load configuration from environment variables.\n"
        f"        Make sure all required ENV vars are set.\n"
        f"        Error: {exc}",
        file=sys.stderr,
    )
    sys.exit(1)
