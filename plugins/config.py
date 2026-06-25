import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.database import get_user_session, save_forward_rule
from info import API_ID, API_HASH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory dictionary to keep track of user configuration state
# Format: {user_id: {"source_chat": chat_id}}
setup_state = {}


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


async def _fetch_chat_buttons(session_string: str, user_id: int, prefix: str) -> list[list[InlineKeyboardButton]]:
    app = Client(
        name=f"temp_fetch_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )

    buttons: list[list[InlineKeyboardButton]] = []
    await app.connect()
    try:
        dialogs = app.get_dialogs(limit=50)
        async for dialog in dialogs:
            chat_title = dialog.chat.title or dialog.chat.first_name or "Unknown Chat"
            chat_title = chat_title[:30]
            buttons.append([InlineKeyboardButton(chat_title, callback_data=f"{prefix}_{dialog.chat.id}")])
    finally:
        await asyncio.sleep(1)
        await _safe_disconnect(app)

    return buttons


@Client.on_message(filters.command("set_forwarding") & filters.private)
async def set_forwarding(client, message):
    user_id = message.from_user.id
    logger.info("User %s initiated the /set_forwarding command.", user_id)

    session_string = get_user_session(user_id)
    if not session_string:
        logger.warning("User %s is not logged in. Prompting login.", user_id)
        await message.reply(
            "<b>❌ You are not logged in.</b>\n\nPlease log in first using /login.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await message.reply("🔄 Fetching your chats, please wait...", parse_mode=ParseMode.HTML)

    try:
        buttons = await _fetch_chat_buttons(session_string, user_id, "src")

        if not buttons:
            await status_msg.edit_text("Could not find any chats to forward from.")
            return

        setup_state[user_id] = {"source_chat": None}

        markup = InlineKeyboardMarkup(buttons)
        logger.info("Sending source chat selection to user %s.", user_id)

        await status_msg.edit_text(
            "<b>Step 1: Select the SOURCE chat (where to copy from):</b>",
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.exception("Failed to fetch dialogs for user %s.", user_id)
        await status_msg.edit_text(f"Error fetching chats: {e}")


@Client.on_callback_query(filters.regex(r"^src_"))
async def select_source(client, callback_query):
    await callback_query.answer()

    user_id = callback_query.from_user.id

    try:
        source_chat_id = _parse_chat_id(callback_query.data, "source")
    except ValueError as err:
        logger.error("Failed to parse source chat ID from callback data: %s", err)
        await callback_query.answer("Error processing chat selection.", show_alert=True)
        return

    session_string = get_user_session(user_id)
    if not session_string:
        await callback_query.answer("Session expired. Please log in again.", show_alert=True)
        return

    setup_state.setdefault(user_id, {})
    setup_state[user_id]["source_chat"] = source_chat_id

    await callback_query.message.edit_text(
        "🔄 Saving source and fetching chats for target...",
        parse_mode=ParseMode.HTML,
    )

    try:
        buttons = await _fetch_chat_buttons(session_string, user_id, "tgt")

        if not buttons:
            await callback_query.message.edit_text("Could not find any chats to select as target.")
            return

        markup = InlineKeyboardMarkup(buttons)
        await callback_query.message.edit_text(
            f"<b>Source Chat ID:</b> <code>{source_chat_id}</code>\n\n"
            f"<b>Step 2: Select the TARGET chat (where to paste):</b>",
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:
        logger.exception("Failed to fetch target chats for user %s.", user_id)
        await callback_query.message.edit_text(f"Error fetching chats: {e}")


@Client.on_callback_query(filters.regex(r"^tgt_"))
async def select_target(client, callback_query):
    await callback_query.answer()

    user_id = callback_query.from_user.id

    try:
        target_chat_id = _parse_chat_id(callback_query.data, "target")
    except ValueError as err:
        logger.error("Failed to parse target chat ID from callback data: %s", err)
        await callback_query.answer("Error processing target selection.", show_alert=True)
        return

    if user_id not in setup_state or "source_chat" not in setup_state[user_id]:
        await callback_query.answer("Session expired. Please run /set_forwarding again.", show_alert=True)
        return

    source_chat_id = setup_state[user_id]["source_chat"]

    if source_chat_id == target_chat_id:
        await callback_query.answer("Source and target chats cannot be the same.", show_alert=True)
        return

    filters_config = {"photo": True, "video": True, "document": True, "text": True}

    logger.info("User %s saved forwarding rule: %s -> %s.", user_id, source_chat_id, target_chat_id)
    save_forward_rule(user_id, source_chat_id, target_chat_id, filters_config)

    setup_state.pop(user_id, None)

    await callback_query.message.edit_text(
        f"<b>✅ Forwarding Rule Saved!</b>\n\n"
        f"<b>Source:</b> <code>{source_chat_id}</code>\n"
        f"<b>Target:</b> <code>{target_chat_id}</code>\n\n"
        f"The background worker will now automatically copy messages between these chats. "
        f"<i>Note: Ensure the daemon process is restarted so it loads the new rules into memory.</i>",
        parse_mode=ParseMode.HTML,
    )
