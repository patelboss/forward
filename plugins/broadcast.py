import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from database.database import get_user_session
from info import API_ID, API_HASH

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Temporary in-memory tracker for interactive user testing selections
# Format: {user_id: {"target_chat": int}}
broadcast_state = {}

@Client.on_message(filters.command("test_send") & filters.private)
async def test_send_cmd(client, message):
    user_id = message.from_user.id
    logger.info(f"User {user_id} triggered conversational testing pipeline via /test_send")

    session_string = get_user_session(user_id)
    if not session_string:
        await message.reply("<b>❌ Error:</b> Please log in first using /login.", parse_mode=ParseMode.HTML)
        return

    status_msg = await message.reply("🔄 Fetching dialog options via your user account credentials...", parse_mode=ParseMode.HTML)

    # Initialize client to pull modern dialog options
    app = Client(
        name=f"temp_tx_fetch_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True
    )

    await app.connect()
    try:
        dialogs = app.get_dialogs(limit=40)
        buttons = []
        async for dialog in dialogs:
            title = dialog.chat.title or dialog.chat.first_name or "Unknown Destination"
            title = title[:30]
            buttons.append([InlineKeyboardButton(title, callback_data=f"tx_{dialog.chat.id}")])

        if not buttons:
            await status_msg.edit_text("No conversational channels discovered on your user profile account.")
            return

        markup = InlineKeyboardMarkup(buttons)
        await status_msg.edit_text("<b>Select the destination chat where your Userbot should post a message:</b>", reply_markup=markup, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Failed loading diagnostic menu: {e}")
        await status_msg.edit_text(f"Error initializing diagnostics: {e}")
    finally:
        if app.is_connected:
            await asyncio.sleep(1)
            await app.disconnect()


@Client.on_callback_query(filters.regex(r"^tx_"))
async def tx_selection_handler(client, callback_query):
    user_id = callback_query.from_user.id
    target_chat_id = int(callback_query.data.split("_"))

    # Store destination configuration state parameters
    broadcast_state[user_id] = {"target_chat": target_chat_id}

    await callback_query.message.delete()

    # Leverage pyromod listen extension (.ask) to wait for incoming interactive response strings
    msg_prompt = await client.ask(
        chat_id=callback_query.message.chat.id,
        text=f"<b>Destination Registered:</b> <code>{target_chat_id}</code>\n\n💬 What message would you like to post there using your Userbot account?",
        parse_mode=ParseMode.HTML
    )
    
    payload_text = msg_prompt.text
    session_string = get_user_session(user_id)

    status_update = await client.send_message(
        chat_id=callback_query.message.chat.id,
        text="🚀 Attempting background transmission via Userbot token session..."
    )

    # Instantiate transmission worker via string parameters
    user_worker = Client(
        name=f"temp_send_worker_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True
    )

    await user_worker.connect()
    try:
        # Fire off payload message using user account authorization signatures
        await user_worker.send_message(chat_id=target_chat_id, text=payload_text)
        logger.info(f"✅ Success! Userbot {user_id} cleanly pushed diagnostic string to {target_chat_id}")
        await status_update.edit_text(f"<b>✅ Message Sent Successfully!</b>\n\nYour personal Userbot account has posted the string directly to Chat ID <code>{target_chat_id}</code>.", parse_mode=ParseMode.HTML)
    except Exception as tx_err:
        logger.error(f"❌ Userbot failure on targeted transmission attempt: {tx_err}")
        await status_update.edit_text(f"<b>❌ Userbot Transmission Failed:</b>\n<code>{tx_err}</code>", parse_mode=ParseMode.HTML)
    finally:
        if user_worker.is_connected:
            await user_worker.disconnect()
        # Flush the conversation memory buffer space
        broadcast_state.pop(user_id, None)
