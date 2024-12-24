from pyrogram import Client
from pyrogram.enums import ParseMode
from config import BOT_TOKEN, API_ID, API_HASH

bot = Client(
    "bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.HTML,
    plugins={"root": "plugins"},
    workers=10
)

if __name__ == "__main__":
    bot.run()
