import asyncio
import logging
from pyromod.listen import Listen
from pyrogram import Client
from pyrogram.enums import ParseMode
from aiohttp import web
from info import BOT_TOKEN, API_ID, API_HASH, LOGGER, BOT_SESSION

# Configure logging programmatically
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("pyrogram").setLevel(logging.ERROR)


class Bot(Client):
    def __init__(self):
        """Initialize the bot with unified event loop."""
        self.LOGGER = LOGGER
        self.LOGGER(__name__).info("Initializing the bot...")

        # Use Listen for pyromod integration
        super().__init__(
            BOT_SESSION,
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            parse_mode=ParseMode.HTML,
            plugins={"root": "plugins"},
            workers=10,
        )

        # Ensure pyromod uses the same event loop
        self.listen = Listen(self)
        self.loop = asyncio.get_event_loop()

        self.LOGGER(__name__).info("Bot initialization complete.")

    async def start(self):
        """Start the bot with unified event loop."""
        self.LOGGER(__name__).info("Starting the bot...")
        try:
            await super().start()
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
    """Starts the health check server."""
    try:
        app = web.Application()
        app.router.add_get("/health", health_check)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
        await site.start()
        return runner  # Return runner for cleanup
    except Exception as e:
        logging.error(f"Failed to start the web server: {e}")


# Main function to run both bot and web server concurrently
async def main():
    """Runs the bot and health check server concurrently with graceful shutdown."""
    bot = Bot()
    runner = None

    try:
        runner = await start_server()
        await asyncio.gather(bot.start(), asyncio.Event().wait())  # Keep running until stopped
    except asyncio.CancelledError:
        logging.info("Shutting down gracefully...")
    finally:
        await bot.stop()  # Cleanly stop the bot
        if runner:
            await runner.cleanup()  # Clean up aiohttp runner resources


# Start asyncio event loop
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Process interrupted by user.")
