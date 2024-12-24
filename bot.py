import asyncio
import logging
import logging.config
from pyrogram import Client
from pyrogram.enums import ParseMode
from aiohttp import web
from pyromod import listen  # type: ignore
from info import BOT_TOKEN, API_ID, API_HASH, LOGGER, BOT_SESSION

# Set up logging
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
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

        # Initialize the client with API credentials and session management
        super().__init__(
            BOT_SESSION,  # Session name or in-memory session
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            parse_mode=ParseMode.HTML,
            plugins={"root": "plugins"},
            workers=10
        )
        self.LOGGER(__name__).info("Bot initialization complete. Ready to start.")

    async def start(self):
        """Start the bot with detailed logging."""
        self.LOGGER(__name__).info("Starting the bot...")
        try:
            await super().start()
            self.LOGGER(__name__).info("Successfully connected to Telegram servers.")

            # Fetch bot information and log it
            me = await self.get_me()
            self.LOGGER(__name__).info(f"Bot details: @{me.username}, {me.first_name}")

        except Exception as e:
            self.LOGGER(__name__).error(f"Error during bot startup: {e}")
            raise

    async def stop(self):
        """Stop the bot with clean shutdown."""
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
    app = web.Application()
    app.router.add_get("/health", health_check)  # /health endpoint
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

# Main function to run both bot and web server concurrently
async def main():
    bot = Bot()
    await asyncio.gather(bot.start(), start_server())  # Run both bot and web server
    await bot.idle()  # Keep the bot running until it is stopped

if __name__ == "__main__":
    asyncio.run(main())  # Start the bot and server concurrently
