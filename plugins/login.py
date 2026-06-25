import asyncio
import logging
import os
import sys

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import (
    FloodWait,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)
from database.database import save_user_session, get_user_session, users_col
from info import API_ID, API_HASH

# Import the running memory cache from your forward plugin so we can kill the client instance
try:
    from plugins.forward import active_userbots
except Exception:
    active_userbots = {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _safe_stop_client(app: Client) -> None:
    try:
        if app.is_connected:
            await app.stop()
    except Exception:
        try:
            if app.is_connected:
                await app.disconnect()
        except Exception:
            logger.exception("Failed to stop/disconnect client cleanly.")


async def _stop_active_userbot(user_id: int) -> None:
    worker_client = active_userbots.get(user_id)
    if not worker_client:
        return

    try:
        if worker_client.is_connected:
            try:
                await worker_client.log_out()
            except Exception:
                await worker_client.stop()
    except Exception:
        logger.warning("Non-critical issue encountered while logging out worker client.", exc_info=True)
    finally:
        active_userbots.pop(user_id, None)


async def _restart_process(client: Client | None = None) -> None:
    try:
        if client is not None:
            await _safe_stop_client(client)
    finally:
        os.execv(sys.executable, [sys.executable] + sys.argv)


@Client.on_message(filters.command("login") & filters.private)
async def login(client, message):
    user_id = message.from_user.id
    logger.info("Login attempt initiated for user %s.", user_id)

    # Check if the user is already logged in
    session = get_user_session(user_id)
    if session:
        logger.info("User %s is already logged in.", user_id)
        await message.reply_text("You're already logged in!", parse_mode=ParseMode.HTML)
        return

    user_client = None
    try:
        await message.reply_text(
            "<b>Enter your phone number with country code (e.g., +1234567890):</b>",
            parse_mode=ParseMode.HTML,
        )
        phone = await client.ask(
            message.chat.id,
            "<b>Please provide your phone number:</b>",
            parse_mode=ParseMode.HTML,
        )
        phone_number = (phone.text or "").strip()

        if not phone_number:
            await message.reply_text(
                "<b>❌ Login failed:</b> Empty phone number received.",
                parse_mode=ParseMode.HTML,
            )
            return

        logger.info("Received phone number from user %s.", user_id)

        user_client = Client(
            name=f"temp_session_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
            device_model="PC 64bit",
            system_version="Windows 11",
            app_version="5.1.1 x64",
        )

        await user_client.connect()
        try:
            logger.info("Sending OTP to phone number for user %s.", user_id)
            sent = await user_client.send_code(phone_number)

            await message.reply_text(
                "<b>Enter the code you received (e.g., 1 2 3 4 5):</b>",
                parse_mode=ParseMode.HTML,
            )
            otp_msg = await client.ask(
                message.chat.id,
                "<b>Enter the OTP code:</b>",
                parse_mode=ParseMode.HTML,
            )
            otp = (otp_msg.text or "").replace(" ", "").strip()

            if not otp:
                await message.reply_text(
                    "<b>❌ Login failed:</b> Empty OTP received.",
                    parse_mode=ParseMode.HTML,
                )
                return

            logger.info("Attempting to sign in user %s with OTP.", user_id)

            try:
                await user_client.sign_in(phone_number, sent.phone_code_hash, otp)
            except SessionPasswordNeeded:
                logger.info("Two-step verification enabled for user %s. Requesting password.", user_id)
                await message.reply_text(
                    "<b>Two-step verification is enabled. Enter your 2FA password/PIN:</b>",
                    parse_mode=ParseMode.HTML,
                )
                pin_msg = await client.ask(
                    message.chat.id,
                    "<b>Please enter your 2-step PIN:</b>",
                    parse_mode=ParseMode.HTML,
                )
                password = (pin_msg.text or "").strip()

                if not password:
                    await message.reply_text(
                        "<b>❌ Login failed:</b> Empty 2FA password received.",
                        parse_mode=ParseMode.HTML,
                    )
                    return

                await user_client.check_password(password)
                logger.info("Two-step verification successful for user %s.", user_id)

            me = await user_client.get_me()
            if not me:
                await message.reply_text(
                    "<b>❌ Login failed:</b> Authorization did not complete.",
                    parse_mode=ParseMode.HTML,
                )
                return

            try:
                session_str = await user_client.export_session_string()
            except Exception:
                # Fallback for older/newer Pyrogram builds if export_session_string is unavailable.
                session_str = getattr(user_client, "session_string", None)

            if not session_str:
                await message.reply_text(
                    "<b>❌ Login failed:</b> Could not export session string.",
                    parse_mode=ParseMode.HTML,
                )
                return

            save_user_session(user_id, session_str)

            logger.info("Login successful for user %s. Restarting process.", user_id)
            await message.reply_text(
                "<b>✅ Login successful! The engine process is restarting now to safely spin up your background forwarder daemon...</b>",
                parse_mode=ParseMode.HTML,
            )

            await _restart_process(client)

        finally:
            await _safe_stop_client(user_client)

    except FloodWait as e:
        logger.warning("FloodWait during login for user %s: %s", user_id, e)
        await message.reply_text(
            f"<b>❌ Login failed:</b> Telegram rate limit. Please wait {e.value} seconds and try again.",
            parse_mode=ParseMode.HTML,
        )
    except PhoneNumberInvalid:
        await message.reply_text(
            "<b>❌ Login failed:</b> Invalid phone number.",
            parse_mode=ParseMode.HTML,
        )
    except PhoneCodeInvalid:
        await message.reply_text(
            "<b>❌ Login failed:</b> Invalid OTP code.",
            parse_mode=ParseMode.HTML,
        )
    except PhoneCodeExpired:
        await message.reply_text(
            "<b>❌ Login failed:</b> OTP code expired. Please run /login again.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error("Login failed for user %s: %s", user_id, e, exc_info=True)
        await message.reply_text(f"<b>❌ Login failed:</b> {e}", parse_mode=ParseMode.HTML)
        try:
            if user_client is not None:
                await _safe_stop_client(user_client)
        except Exception:
            pass


# =====================================================================
# HARD RESTART WITHOUT REDEPLOY COMMAND
# =====================================================================
@Client.on_message(filters.command("restart") & filters.private)
async def hard_restart(client, message):
    user_id = message.from_user.id
    logger.info("🔄 User %s triggered an engine memory soft-restart via command.", user_id)

    await message.reply_text(
        "<b>🔄 Initializing Engine Restart...</b>\n\n"
        "Flushing running RAM and reloading all background client listeners directly from MongoDB. "
        "Please wait a few seconds.",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Stop background userbot workers first so the process restarts cleanly.
        for uid in list(active_userbots.keys()):
            await _stop_active_userbot(uid)
    except Exception:
        logger.exception("Error while stopping active userbots before restart.")

    await _restart_process(client)


# =====================================================================
# LOGOUT & PURGE SESSION COMMAND
# =====================================================================
@Client.on_message(filters.command("logout") & filters.private)
async def logout_cmd(client, message):
    user_id = message.from_user.id
    logger.info("🚪 User %s requested logout sequence execution.", user_id)

    session = get_user_session(user_id)
    if not session:
        await message.reply_text(
            "<b>⚠️ Error:</b> You are not currently logged in.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await message.reply_text(
        "⚙️ Unlinking account sessions and flushing memory pools...",
        parse_mode=ParseMode.HTML,
    )

    # Stop and remove the active background worker for this user, if present.
    await _stop_active_userbot(user_id)

    # Permanently wipe the session string record out of MongoDB collection.
    try:
        users_col.delete_one({"user_id": user_id})
        logger.info("Session completely dropped from MongoDB for user %s", user_id)

        await status_msg.edit_text(
            "<b>🚪 Logout Successful!</b>\n\n"
            "Your personal user account has been completely unlinked, your session string was permanently "
            "deleted from MongoDB, and all active background listening threads have been shut down.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error("Failed to clear database record during logout: %s", e, exc_info=True)
        await status_msg.edit_text(
            f"Logged out from runtime memory, but failed to drop database document row: {e}",
            parse_mode=ParseMode.HTML,
        )
