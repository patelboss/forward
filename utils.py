async def listen(chat_id, timeout=60):
    """Wait for user input."""
    from asyncio import TimeoutError
    from pyrogram import filters
    from bot import bot

    try:
        response = await bot.listen(chat_id, filters=filters.text, timeout=timeout)
        return response.text
    except TimeoutError:
        return None
