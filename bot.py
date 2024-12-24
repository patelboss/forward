import asyncio
from aiohttp import web
from pyrogram import Client
import uvloop
from info import *  # Ensure you have the correct environment variable imports
import pyromod.listen
# Define the bot
class MyBot(Client):
    def __init__(self):
        super().__init__(
            "telegram_forwarder_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
        )

    async def start(self):
        await super().start()  # Initialize the client
        me = await self.get_me()  # Fetch bot details
        print(f"Bot started successfully with username: {me.username}")  # Log username

# Define the health check endpoint
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
    bot = MyBot()

    # Start both the bot and web server concurrently
    await asyncio.gather(bot.start(), start_server())

    # The 'idle' function is essential to keep the bot running
    await bot.idle()

# Entry point
if __name__ == "__main__":
    uvloop.install()  # Optional: use uvloop for better performance
    loop = asyncio.get_event_loop()  # Use the current event loop
    loop.run_until_complete(main())  # Run the main async function
