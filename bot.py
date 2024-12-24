import asyncio
import logging
from pyrogram import Client
from pyrogram.enums import ParseMode
from aiohttp import web
from info import BOT_TOKEN, API_ID, API_HASH, LOGGER, BOT_SESSION
from pyromod import listen 
import os
# Configure logging programmatically
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("pyrogram").setLevel(logging.ERROR)

# Define the Bot class
class Bot(Client):
    def __init__(self):
        """Initialize the bot with enhanced logging."""
        self.LOGGER = LOGGER
        self.LOGGER(__name__).info("Initializing the bot...")

        # Log session information
        if BOT_SESSION:
            self.LOGGER(__name__).info("Using the provided BOT_SESSION for the bot.")
        else:
            self.LOGGER(__name__).warning("No BOT_SESSION provided. Using an in-memory session temporarily.")

        super().__init__(
            BOT_SESSION,
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            parse_mode=ParseMode.HTML,
            plugins={"root": "plugins"},
            workers=10,
        )
        self.LOGGER(__name__).info("Bot initialization complete. Ready to start.")

    async def start(self):
        self.LOGGER(__name__).info("Starting the bot...")
        try:
            await super().start()
            me = await self.get_me()
            self.LOGGER(__name__).info(f"Bot details: @{me.username}, {me.first_name}")
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

# Health check for aiohttp
async def health_check(request):
    return web.Response(text="OK")

# Start aiohttp web server
async def start_server():
    try:
        app = web.Application()
        app.router.add_get("/health", health_check)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
        await site.start()
    except Exception as e:
        logging.error(f"Failed to start the web server: {e}")

# Main function to run both bot and web server concurrently
async def main():
    bot = Bot()
    await asyncio.gather(bot.start(), start_server())
    await bot.idle()

if __name__ == "__main__":
    asyncio.run(main())
