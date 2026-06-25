import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.database import get_user_session
from info import API_ID, API_HASH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Temporary in-memory tracker for interactive user testing selections
# Format: {user_id: {"target_chat": int}}
broadcast_state = {}


async def _safe_disconnect(app: Client) -> None:
    try:
        if app.is_connected:
            await app.disconnect()
    except Exception:
        logger.exception("Failed to disconnect temporary client cleanly.")


def _parse_chat_id(callback_data: str, prefix: str) -> int:
    try:
        raw_id = callback_data.split("_", 1)[1]
        return int(raw_id)
    except (IndexError, ValueError) as err:
        raise ValueError(f"Invalid callback data for {prefix}: {callback_data}") from err


async def _fetch_dialog_buttons(session_string: str, user_id: int) -> list[list[InlineKeyboardButton]]:
    app = Client(
        name=f"temp_tx_fetch_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )

    buttons: list[list[InlineKeyboardButton]] = []
    await app.connect()
    try:
        dialogs = app.get_dialogs(limit=40)
        async for dialog in dialogs:
            title = dialog.chat.title or dialog.chat.first_name or "Unknown Destination"
            title = title[:30]
            buttons.append([InlineKeyboardButton(title, callback_data=f"tx_{dialog.chat.id}")])
    finally:
        await asyncio.sleep(1)
        await _safe_disconnect(app)

    return buttons


@Client.on_message(filters.command("test_send") & filters.private)
async def test_send_cmd(client, message):
    user_id = message.from_user.id
    logger.info("User %s triggered conversational testing pipeline via /test_send", user_id)

    session_string = get_user_session(user_id)
    if not session_string:
        await message.reply(
            "<b>❌ Error:</b> Please log in first using /login.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await message.reply(
        "🔄 Fetching dialog options via your user account credentials...",
        parse_mode=ParseMode.HTML,
    )

    try:
        buttons = await _fetch_dialog_buttons(session_string, user_id)

        if not buttons:
            await status_msg.edit_text(
                "No conversational channels discovered on your user profile account.",
                parse_mode=ParseMode.HTML,
            )
            return

        markup = InlineKeyboardMarkup(buttons)
        await status_msg.edit_text(
            "<b>Select the destination chat where your Userbot should post a message:</b>",
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.exception("Failed loading diagnostic menu for user %s.", user_id)
        try:
            await status_msg.edit_text(f"Error initializing diagnostics: {e}", parse_mode=ParseMode.HTML)
        except Exception:
            pass


@Client.on_callback_query(filters.regex(r"^tx_"))
async def tx_selection_handler(client, callback_query):
    await callback_query.answer()

    user_id = callback_query.from_user.id

    try:
        target_chat_id = _parse_chat_id(callback_query.data, "target")
    except ValueError as err:
        logger.error("Failed to parse target chat ID from callback data: %s", err)
        await callback_query.answer("Error processing destination selection.", show_alert=True)
        return

    session_string = get_user_session(user_id)
    if not session_string:
        await callback_query.answer("Session expired. Please log in again using /login.", show_alert=True)
        return

    broadcast_state[user_id] = {"target_chat": target_chat_id}

    try:
        await callback_query.message.delete()
    except Exception:
        pass

    try:
        msg_prompt = await client.ask(
            chat_id=callback_query.message.chat.id,
            text=(
                f"<b>Destination Registered:</b> <code>{target_chat_id}</code>\n\n"
                "💬 What message would you like to post there using your Userbot account?"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as ask_err:
        logger.error("Prompt interaction failed for user %s: %s", user_id, ask_err)
        broadcast_state.pop(user_id, None)
        await client.send_message(
            chat_id=callback_query.message.chat.id,
            text=f"<b>❌ Could not read your message:</b>\n<code>{ask_err}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    payload_text = (msg_prompt.text or msg_prompt.caption or "").strip()
    if not payload_text:
        broadcast_state.pop(user_id, None)
        await client.send_message(
            chat_id=callback_query.message.chat.id,
            text="<b>❌ Empty message received.</b>\nPlease run /test_send again and send some text.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_update = await client.send_message(
        chat_id=callback_query.message.chat.id,
        text="🚀 Attempting background transmission via Userbot token session...",
    )

    user_worker = Client(
        name=f"temp_send_worker_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )

    await user_worker.connect()
    try:
        await user_worker.send_message(chat_id=target_chat_id, text=payload_text)
        logger.info("✅ Success! Userbot %s posted a test message to %s", user_id, target_chat_id)

        await status_update.edit_text(
            f"<b>✅ Message Sent Successfully!</b>\n\n"
            f"Your personal Userbot account has posted the string directly to Chat ID <code>{target_chat_id}</code>.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as tx_err:
        logger.exception("❌ Userbot transmission failed for user %s.", user_id)
        await status_update.edit_text(
            f"<b>❌ Userbot Transmission Failed:</b>\n<code>{tx_err}</code>",
            parse_mode=ParseMode.HTML,
        )
    finally:
        broadcast_state.pop(user_id, None)
        await asyncio.sleep(1)
        await _safe_disconnect(user_worker)
