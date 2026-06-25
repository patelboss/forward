"""
database.py
-----------
Async MongoDB persistence layer using Motor (the async PyMongo driver).

Collection schema (sessions):
    {
        "_id":            <int>   Telegram user_id,
        "session_string": <str>   Pyrogram StringSession export,
        "is_active":      <bool>  True when the worker should be running
    }

All public helpers are coroutines – await them from async contexts.
"""

import sys
from typing import Optional

import motor.motor_asyncio

from config import cfg

# ── Module-level client / db / collection ────────────────────────────────────
_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_collection: Optional[motor.motor_asyncio.AsyncIOMotorCollection] = None


async def init_db() -> None:
    """
    Create the Motor client and cache the target collection reference.
    Call this once at application startup (inside main.py).
    """
    global _client, _collection

    try:
        _client = motor.motor_asyncio.AsyncIOMotorClient(cfg.MONGO_URI)
        db = _client[cfg.MONGO_DB_NAME]
        _collection = db[cfg.MONGO_COLLECTION]

        # Lightweight connectivity check – raises if URI is wrong
        await _client.admin.command("ping")
        print("[DB] Connected to MongoDB Atlas successfully.", flush=True)
    except Exception as exc:
        print(f"[DB][ERROR] MongoDB connection failed: {exc}", file=sys.stderr)
        raise


def _col() -> motor.motor_asyncio.AsyncIOMotorCollection:
    """Return the cached collection, raising if init_db() was never called."""
    if _collection is None:
        raise RuntimeError("Database not initialised. Call await init_db() first.")
    return _collection


# ── CRUD helpers ──────────────────────────────────────────────────────────────


async def save_session(user_id: int, session_string: str) -> None:
    """
    Insert or fully replace the session document for *user_id*.
    Sets is_active=True automatically so the worker daemon picks it up on
    the next boot cycle.
    """
    doc = {
        "_id": user_id,
        "session_string": session_string,
        "is_active": True,
    }
    await _col().replace_one({"_id": user_id}, doc, upsert=True)
    print(f"[DB] Session saved for user_id={user_id}.", flush=True)


async def get_session(user_id: int) -> Optional[dict]:
    """
    Fetch the session document for *user_id*.
    Returns the full document dict, or None if not found.
    """
    return await _col().find_one({"_id": user_id})


async def get_active_session() -> Optional[dict]:
    """
    Return the first document where is_active=True.
    In a single-user deployment there will be at most one such document.
    """
    return await _col().find_one({"is_active": True})


async def delete_session(user_id: int) -> None:
    """
    Remove the session document for *user_id* entirely.
    Use this when the user sends /logout.
    """
    result = await _col().delete_one({"_id": user_id})
    if result.deleted_count:
        print(f"[DB] Session deleted for user_id={user_id}.", flush=True)
    else:
        print(f"[DB] No session found to delete for user_id={user_id}.", flush=True)


async def set_active(user_id: int, active: bool) -> None:
    """Toggle the is_active flag without touching the session_string."""
    await _col().update_one({"_id": user_id}, {"$set": {"is_active": active}})


async def close_db() -> None:
    """Gracefully close the Motor client. Called during shutdown."""
    global _client
    if _client:
        _client.close()
        print("[DB] MongoDB connection closed.", flush=True)
