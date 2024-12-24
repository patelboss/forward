import asyncio
from aiohttp import web
from pyrogram import Client
from pyromod import listen
from info import API_ID, API_HASH, BOT_TOKEN

# Define the bot
class MyBot(Client):
    def __init__(self):
        super().__init__(
            "telegram_forwarder_bot",  # Session name
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
        )

    async def start(self):
        """Override start method to start the bot."""
        await super().start()  # Ensure the pyrogram Client starts properly
        print(f"Bot started successfully with username: {self.username}")

# Initialize the bot
bot = MyBot()

# Define the health check endpoint for the web server
async def health_check(request):
    return web.Response(text="OK")

# Start the aiohttp web server
async def start_server():
    app = web.Application()
    app.router.add_get("/health", health_check)  # Add a /health endpoint
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

# Run the bot and web server concurrently
async def main():
    await asyncio.gather(bot.start(), start_server())  # Run both bot and web server
    await bot.idle()  # Keep the bot running until manually stopped

# Entry point
if __name__ == "__main__":
    asyncio.run(main())  # Run the main async function
