from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.database import save_forward_rule, get_user_session

@Client.on_message(filters.command("set_forwarding"))
async def set_forwarding(client, message):
    session = get_user_session(message.from_user.id)
    if not session:
        await message.reply("Please log in first using /login.")
        return

    app = Client(session, api_id=client.api_id, api_hash=client.api_hash)
    async with app:
        chats = await app.get_dialogs()

        buttons = [
            [InlineKeyboardButton(chat.chat.title or chat.chat.first_name, callback_data=str(chat.chat.id))]
            for chat in chats
        ]
        markup = InlineKeyboardMarkup(buttons)

        await message.reply("Select a source chat:", reply_markup=markup)

@Client.on_callback_query()
async def set_target(client, callback_query):
    data = int(callback_query.data)

    # Save the source or target chat
    if not hasattr(callback_query.message, "source_chat"):
        callback_query.message.source_chat = data
        await callback_query.message.reply("Now select a target chat:")
    else:
        save_forward_rule(callback_query.from_user.id, callback_query.message.source_chat, data, {})
        await callback_query.message.reply("Forwarding rule saved!")
