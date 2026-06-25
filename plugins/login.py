"""
plugins/login.py
----------------
Conversational /login flow for the Master Bot.

State machine (per-user, stored in memory dict):
    IDLE        → user sends /login
    WAIT_PHONE  → bot asks for phone number
    WAIT_CODE   → bot asks for OTP code
    WAIT_2FA    → (optional) bot asks for 2-FA password

On successful login the StringSession is persisted to MongoDB and the process
performs a clean self-restart via os.execv so the forwarder daemon picks up
the fresh session on the next boot without stale in-memory state.

Also registers:
    /logout  – removes session from DB and kills the worker if running.
    /status  – shows whether a session is currently active.
"""

import os
import sys
import asyncio
from typing import Dict, Any

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
)

from config import cfg
from database import save_session, get_session, delete_session, get_active_session

# ── Conversation state per user_id ───────────────────────────────────────────
# Keys: user_id (int)
# Values: dict with keys: "state", "phone", "phone_code_hash", "client"
_state: Dict[int, Dict[str, Any]] = {}

# State identifiers
IDLE = "idle"
WAIT_PHONE = "wait_phone"
WAIT_CODE = "wait_code"
WAIT_2FA = "wait_2fa"


def _make_ephemeral_client() -> Client:
    """
    Create a short-lived, in-memory Pyrogram client that mimics a real
    desktop installation (avoids Telegram's new-client detection).
    Uses ":memory:" so no session file is created on disk.
    """
    return Client(
        name=":memory:",
        api_id=cfg.API_ID,
        api_hash=cfg.API_HASH,
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.1.1 x64",
        # No bot_token → user-mode client
    )


def _clean_code(raw: str) -> str:
    """
    Strip spaces that Telegram sometimes inserts into forwarded OTP codes
    (anti-spam measure), e.g. "1 2 3 4 5" → "12345".
    """
    return raw.replace(" ", "").strip()


# ─────────────────────────────────────────────────────────────────────────────
# /login  –  entry point
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_login(client: Client, message: Message) -> None:
    """Handle the /login command – begin auth flow."""
    uid = message.from_user.id

    # Check if already logged in
    existing = await get_session(uid)
    if existing and existing.get("is_active"):
        await message.reply_text(
            "✅ You already have an active session.\n"
            "Use /logout first if you want to re-authenticate."
        )
        return

    _state[uid] = {"state": WAIT_PHONE}
    await message.reply_text(
        "📱 Please send your phone number in international format.\n"
        "Example: `+919876543210`",
        quote=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Generic text handler – routes through the state machine
# ─────────────────────────────────────────────────────────────────────────────

async def conversation_router(client: Client, message: Message) -> None:
    """
    Central router for all incoming text messages during an active
    login conversation.  Ignores messages from users not in a flow.
    """
    uid = message.from_user.id
    user_state = _state.get(uid, {})
    state = user_state.get("state", IDLE)

    if state == WAIT_PHONE:
        await _handle_phone(client, message, uid, user_state)
    elif state == WAIT_CODE:
        await _handle_code(client, message, uid, user_state)
    elif state == WAIT_2FA:
        await _handle_2fa(client, message, uid, user_state)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Receive phone number → send OTP
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_phone(
    client: Client, message: Message, uid: int, user_state: dict
) -> None:
    phone = message.text.strip()

    ephemeral = _make_ephemeral_client()

    try:
        await ephemeral.connect()
        sent = await ephemeral.send_code(phone)
    except PhoneNumberInvalid:
        await message.reply_text("❌ Invalid phone number. Please try again.")
        return
    except Exception as exc:
        print(f"[LOGIN][ERROR] send_code failed: {exc}", file=sys.stderr)
        await message.reply_text(f"❌ Error contacting Telegram: `{exc}`")
        _state.pop(uid, None)
        return

    # Save ephemeral client and phone_code_hash so next step can sign in
    user_state.update(
        {
            "state": WAIT_CODE,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "client": ephemeral,
        }
    )
    _state[uid] = user_state

    await message.reply_text(
        "🔑 OTP sent to your Telegram account.\n"
        "Please send the code you received.\n"
        "_(If the code has spaces like `1 2 3 4 5`, send it as-is — I'll clean it.)_",
        quote=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Receive OTP → sign in
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_code(
    client: Client, message: Message, uid: int, user_state: dict
) -> None:
    code = _clean_code(message.text)
    ephemeral: Client = user_state["client"]
    phone: str = user_state["phone"]
    phone_code_hash: str = user_state["phone_code_hash"]

    try:
        await ephemeral.sign_in(
            phone_number=phone,
            phone_code_hash=phone_code_hash,
            phone_code=code,
        )
    except PhoneCodeInvalid:
        await message.reply_text("❌ Invalid code. Please re-send the correct OTP.")
        return
    except PhoneCodeExpired:
        await message.reply_text(
            "❌ The OTP has expired. Use /login to start over."
        )
        _state.pop(uid, None)
        await ephemeral.disconnect()
        return
    except SessionPasswordNeeded:
        # Account has 2-FA enabled
        user_state["state"] = WAIT_2FA
        _state[uid] = user_state
        await message.reply_text(
            "🔐 Your account has Two-Factor Authentication enabled.\n"
            "Please send your 2-FA password now.",
            quote=True,
        )
        return
    except Exception as exc:
        print(f"[LOGIN][ERROR] sign_in failed: {exc}", file=sys.stderr)
        await message.reply_text(f"❌ Sign-in error: `{exc}`")
        _state.pop(uid, None)
        await ephemeral.disconnect()
        return

    await _finalise_login(client, message, uid, ephemeral)


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 (optional): Receive 2-FA password
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_2fa(
    client: Client, message: Message, uid: int, user_state: dict
) -> None:
    password = message.text.strip()
    ephemeral: Client = user_state["client"]

    try:
        await ephemeral.check_password(password)
    except PasswordHashInvalid:
        await message.reply_text(
            "❌ Incorrect 2-FA password. Please try again."
        )
        return
    except Exception as exc:
        print(f"[LOGIN][ERROR] check_password failed: {exc}", file=sys.stderr)
        await message.reply_text(f"❌ 2-FA error: `{exc}`")
        _state.pop(uid, None)
        await ephemeral.disconnect()
        return

    await _finalise_login(client, message, uid, ephemeral)


# ─────────────────────────────────────────────────────────────────────────────
# Finalise: export session → save to DB → self-restart
# ─────────────────────────────────────────────────────────────────────────────

async def _finalise_login(
    bot: Client, message: Message, uid: int, ephemeral: Client
) -> None:
    """Export StringSession, persist to MongoDB, and perform hard restart."""
    try:
        session_string = await ephemeral.export_session_string()
        await ephemeral.disconnect()

        await save_session(uid, session_string)
        _state.pop(uid, None)

        await message.reply_text(
            "✅ Login successful! Session saved.\n"
            "🔄 Restarting the worker daemon now — forwarding will begin shortly…"
        )

        # Give Pyrogram a moment to deliver the reply before we replace the process
        await asyncio.sleep(2)

    except Exception as exc:
        print(f"[LOGIN][ERROR] Finalise failed: {exc}", file=sys.stderr)
        await message.reply_text(f"❌ Could not save session: `{exc}`")
        return

    # ── Hard self-restart via os.execv ────────────────────────────────────────
    # This completely replaces the current process image with a fresh Python
    # interpreter running the same entry point with the same argv.
    # The new process will boot, load the session from MongoDB, and start the
    # forwarder daemon cleanly.
    print("[LOGIN] Performing self-restart via os.execv …", flush=True)
    os.execv(sys.executable, [sys.executable] + sys.argv)


# ─────────────────────────────────────────────────────────────────────────────
# /logout
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_logout(client: Client, message: Message) -> None:
    """Remove the session from the database. Worker stops on next restart."""
    uid = message.from_user.id
    session = await get_session(uid)
    if not session:
        await message.reply_text("⚠️ No active session found for your account.")
        return

    await delete_session(uid)
    await message.reply_text(
        "🗑️ Session deleted.\n"
        "The forwarder daemon will stop on the next restart.\n"
        "Use /login to re-authenticate."
    )


# ─────────────────────────────────────────────────────────────────────────────
# /status
# ─────────────────────────────────────────────────────────────────────────────

async def cmd_status(client: Client, message: Message) -> None:
    """Report session status for the requesting user."""
    uid = message.from_user.id
    session = await get_session(uid)

    if not session:
        status_text = "❌ No session stored for your account."
    elif session.get("is_active"):
        status_text = "✅ Active session found. Forwarder daemon is (or will be) running."
    else:
        status_text = "⚠️ Session exists but is marked inactive."

    await message.reply_text(status_text)


# ─────────────────────────────────────────────────────────────────────────────
# Plugin registration – called by main.py dynamic loader
# ─────────────────────────────────────────────────────────────────────────────

def register(app: Client) -> None:
    """
    Attach all handlers to the Master Bot client.
    main.py calls register(bot) for every plugin it discovers.
    """
    app.add_handler(
        # /login command
        __import__("pyrogram.handlers", fromlist=["MessageHandler"]).MessageHandler(
            cmd_login,
            filters.command("login") & filters.private,
        )
    )
    app.add_handler(
        __import__("pyrogram.handlers", fromlist=["MessageHandler"]).MessageHandler(
            cmd_logout,
            filters.command("logout") & filters.private,
        )
    )
    app.add_handler(
        __import__("pyrogram.handlers", fromlist=["MessageHandler"]).MessageHandler(
            cmd_status,
            filters.command("status") & filters.private,
        )
    )
    # Catch-all text handler for in-progress conversations
    # Lower priority (group=1) so it doesn't intercept commands
    app.add_handler(
        __import__("pyrogram.handlers", fromlist=["MessageHandler"]).MessageHandler(
            conversation_router,
            filters.text & filters.private & ~filters.command(["login", "logout", "status"]),
        ),
        group=1,
    )

    print("[PLUGIN] login.py registered (/login, /logout, /status).", flush=True)
