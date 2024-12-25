import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.database import save_forward_rule, get_user_session

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@Client.on_message(filters.command("set_forwarding"))
async def set_forwarding(client, message):
    user_id = message.from_user.id
    logger.info(f"User {user_id} initiated the /set_forwarding command.")

    session = get_user_session(user_id)
    if not session:
        logger.warning(f"User {user_id} is not logged in. Prompting login.")
        await message.reply("Please log in first using /login.")
        return

    # Create a temporary client instance with the user's session
    app = Client(session, api_id=client.api_id, api_hash=client.api_hash)
    async with app:
        # Fetch user chats
        logger.info(f"Fetching chats for user {user_id}.")
        chats = await app.get_dialogs()

        # Prepare inline buttons for chat selection
        buttons = [
            [InlineKeyboardButton(chat.chat.title or chat.chat.first_name, callback_data=str(chat.chat.id))]
            for chat in chats
        ]
        markup = InlineKeyboardMarkup(buttons)

        logger.info(f"Sending source chat selection to user {user_id}.")
        await message.reply("Select a source chat:", reply_markup=markup)

@Client.on_callback_query()
async def set_target(client, callback_query):
    user_id = callback_query.from_user.id
    data = int(callback_query.data)

    logger.info(f"User {user_id} selected chat with ID {data}.")

    # Save the source or target chat
    if not hasattr(callback_query.message, "source_chat"):
        logger.info(f"User {user_id} selected source chat {data}. Asking for target chat.")
        callback_query.message.source_chat = data
        await callback_query.message.reply("Now select a target chat:")
    else:
        # Save the forwarding rule
        logger.info(f"User {user_id} saved forwarding rule: source chat {callback_query.message.source_chat} -> target chat {data}.")
        save_forward_rule(user_id, callback_query.message.source_chat, data, {})
        await callback_query.message.reply("Forwarding rule saved!")
