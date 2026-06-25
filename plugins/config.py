import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from database.database import save_forward_rule, get_user_session
from info import API_ID, API_HASH

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory dictionary to keep track of user configuration state
# Format: {user_id: {"source_chat": chat_id}}
setup_state = {}

@Client.on_message(filters.command("set_forwarding") & filters.private)
async def set_forwarding(client, message):
    user_id = message.from_user.id
    logger.info(f"User {user_id} initiated the /set_forwarding command.")

    session_string = get_user_session(user_id)
    if not session_string:
        logger.warning(f"User {user_id} is not logged in. Prompting login.")
        await message.reply("<b>❌ You are not logged in.</b>\n\nPlease log in first using /login.", parse_mode=ParseMode.HTML)
        return

    status_msg = await message.reply("🔄 Fetching your chats, please wait...", parse_mode=ParseMode.HTML)

    # Create a temporary client instance using the user's string session
    app = Client(
        name=f"temp_fetch_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True
    )
    
    await app.connect()
    try:
        logger.info(f"Fetching chats for user {user_id}.")
        
        # Fetch dialogs (limit to 50 to avoid massive inline keyboards and timeout errors)
        dialogs = app.get_dialogs(limit=50)
        
        buttons = []
        async for dialog in dialogs:
            # Safely grab the title or first name
            chat_title = dialog.chat.title or dialog.chat.first_name or "Unknown Chat"
            # Truncate title to avoid Telegram button rendering limits
            chat_title = chat_title[:30] 
            buttons.append([InlineKeyboardButton(chat_title, callback_data=f"src_{dialog.chat.id}")])
        
        if not buttons:
            await status_msg.edit_text("Could not find any chats to forward from.")
            return

        markup = InlineKeyboardMarkup(buttons)

        # Initialize state tracker for this specific user
        setup_state[user_id] = {"source_chat": None}

        logger.info(f"Sending source chat selection to user {user_id}.")
        await status_msg.edit_text("<b>Step 1: Select the SOURCE chat (where to copy from):</b>", reply_markup=markup, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logger.error(f"Failed to fetch dialogs: {e}")
        await status_msg.edit_text(f"Error fetching chats: {e}")
    finally:
        if app.is_connected:
            await app.disconnect()


@Client.on_callback_query(filters.regex(r"^src_"))
async def select_source(client, callback_query):
    user_id = callback_query.from_user.id
    source_chat_id = int(callback_query.data.split("_"))
    
    if user_id not in setup_state:
        setup_state[user_id] = {}
        
    setup_state[user_id]["source_chat"] = source_chat_id
    
    session_string = get_user_session(user_id)
    
    await callback_query.message.edit_text("🔄 Saving source and fetching chats for target...", parse_mode=ParseMode.HTML)
    
    # Re-initialize to fetch the list again for the TARGET chat selection
    app = Client(
        name=f"temp_fetch_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True
    )
    
    await app.connect()
    try:
        dialogs = app.get_dialogs(limit=50)
        buttons = []
        async for dialog in dialogs:
            chat_title = dialog.chat.title or dialog.chat.first_name or "Unknown Chat"
            chat_title = chat_title[:30] 
            buttons.append([InlineKeyboardButton(chat_title, callback_data=f"tgt_{dialog.chat.id}")])
        
        markup = InlineKeyboardMarkup(buttons)
        await callback_query.message.edit_text(
            f"<b>Source Chat ID:</b> <code>{source_chat_id}</code>\n\n<b>Step 2: Select the TARGET chat (where to paste):</b>", 
            reply_markup=markup,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await callback_query.message.edit_text(f"Error fetching chats: {e}")
    finally:
        if app.is_connected:
            await app.disconnect()


@Client.on_callback_query(filters.regex(r"^tgt_"))
async def select_target(client, callback_query):
    user_id = callback_query.from_user.id
    target_chat_id = int(callback_query.data.split("_"))
    
    # Safety check in case the bot restarted mid-setup
    if user_id not in setup_state or "source_chat" not in setup_state[user_id]:
        await callback_query.answer("Session expired. Please run /set_forwarding again.", show_alert=True)
        return
        
    source_chat_id = setup_state[user_id]["source_chat"]
    
    # Define default filter configuration
    filters_config = {"photo": True, "video": True, "document": True, "text": True}
    
    logger.info(f"User {user_id} saved forwarding rule: {source_chat_id} -> {target_chat_id}.")
    save_forward_rule(user_id, source_chat_id, target_chat_id, filters_config)
    
    # Clean up the state memory
    del setup_state[user_id]
    
    await callback_query.message.edit_text(
        f"<b>✅ Forwarding Rule Saved!</b>\n\n"
        f"<b>Source:</b> <code>{source_chat_id}</code>\n"
        f"<b>Target:</b> <code>{target_chat_id}</code>\n\n"
        f"The background worker will now automatically copy messages between these chats. <i>Note: Ensure the daemon process is restarted so it loads the new rules into memory.</i>",
        parse_mode=ParseMode.HTML
    )
