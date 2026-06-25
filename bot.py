from pyromod import listen
import os
import logging
import asyncio
from pyrogram import Client
from pyrogram.enums import ParseMode
from flask import Flask, jsonify
from info import BOT_TOKEN, API_ID, API_HASH, LOGGER, BOT_SESSION
from threading import Thread

# Import our bootloader function from our newly updated plugins/forward file
from plugins.forward import boot_userbots

# Configure logging programmatically
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

class Bot(Client):
    def __init__(self):
        self.LOGGER = LOGGER
        self.LOGGER(__name__).info("Initializing the bot...")

        super().__init__(
            BOT_SESSION,
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            parse_mode=ParseMode.HTML,
            plugins={"root": "plugins"},
            workers=50,
        )
        self.LOGGER(__name__).info("Bot initialization complete. Ready to start.")

    async def start(self):
        self.LOGGER(__name__).info("Starting the bot...")
        try:
            await super().start()
            me = await self.get_me()
            self.LOGGER(__name__).info(f"Bot details: @{me.username}, {me.first_name}")
            
            # ✅ FIXED: Run the background userbot engines safely inside the running loop
            asyncio.create_task(boot_userbots())
            
        except Exception as e:
            self.LOGGER(__name__).error(f"Error during bot startup: {e}")
            raise

    async def stop(self):
        self.LOGGER(__name__).info("Stopping the bot...")
        try:
            await super().stop()
            self.LOGGER(__name__).info("Bot disconnected from Telegram servers.")
        except Exception as e:
            self.LOGGER(__name__).error(f"Error during bot shutdown: {e}")

# Health check for Flask (Bypasses health checks on Koyeb)
app = Flask(__name__)

@app.route('/health', methods=['GET'])
@app.route('/', methods=['GET'])
def health_check():
    return jsonify({"status": "OK"}), 200

def start_flask_server():
    try:
        port = int(os.getenv("PORT", 8080))
        app.run(host="0.0.0.0", port=port)
    except Exception as e:
        logging.error(f"Failed to start the Flask server: {e}")

def main():
    bot = Bot()

    flask_thread = Thread(target=start_flask_server)
    flask_thread.daemon = True  
    flask_thread.start()

    bot.run()

if __name__ == "__main__":
    main()
