import asyncio
from aiohttp import web
from pyrogram import Client
from config import API_ID, API_HASH, BOT_TOKEN

# Define the bot
bot = Client(
    "telegram_forwarder_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# Define the health check endpoint
async def health_check(request):
    return web.Response(text="OK")

# Start the aiohttp web server
async def start_server():
    app = web.Application()
    app.router.add_get("/health", health_check)  # Add a /health endpoint
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()

# Run the bot and web server concurrently
async def main():
    await asyncio.gather(bot.start(), start_server())
    await bot.idle()

# Entry point
if __name__ == "__main__":
    asyncio.run(main())
