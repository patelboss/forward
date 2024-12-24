from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import listen
from database.database import save_user_session, get_user_session

@Client.on_message(filters.command("login"))
async def login(client, message):
    user_id = message.from_user.id

    session = get_user_session(user_id)
    if session:
        await message.reply("You're already logged in!")
        return

    await message.reply("Enter your phone number:")
    phone = await listen(message.chat.id)

    app = Client(":memory:", api_id=client.api_id, api_hash=client.api_hash)
    async with app:
        sent = await app.send_code(phone)
        await message.reply("Enter the code you received:")
        code = await listen(message.chat.id)

        try:
            await app.sign_in(phone, sent.phone_code_hash, code)
            save_user_session(user_id, app.export_session_string())
            await message.reply("Login successful!")
        except Exception as e:
            await message.reply(f"Login failed: {e}")
