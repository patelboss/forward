from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import listen
from database.database import save_user_session, get_user_session

@Client.on_message(filters.command("login"))
async def login(client, message):
    user_id = message.from_user.id

    # Check if the user is already logged in
    session = get_user_session(user_id)
    if session:
        await message.reply("You're already logged in!")
        return

    # Step 1: Ask for the phone number
    await message.reply("Enter your phone number:")
    phone = await listen(message.chat.id)

    # Create a temporary client instance
    app = Client(":memory:", api_id=client.api_id, api_hash=client.api_hash)
    async with app:
        # Send the OTP to the provided phone number
        sent = await app.send_code(phone)
        await message.reply("Enter the code you received (e.g., 1 2 3 4 5):")
        
        # Step 2: Wait for OTP input with spaces
        otp = await listen(message.chat.id)
        otp = otp.replace(" ", "")  # Remove spaces for verification

        try:
            # Step 3: Sign in using the OTP
            await app.sign_in(phone, sent.phone_code_hash, otp)
            
            # Step 4: Ask for a 2-step verification PIN if enabled
            if app.is_authorized() and app.me.two_step_verification_enabled:
                await message.reply("Two-step verification is enabled. Enter your PIN:")
                pin = await listen(message.chat.id)
                await app.check_password(pin)
                await message.reply("Two-step verification successful!")

            # Step 5: Save session and complete the login
            save_user_session(user_id, app.export_session_string())
            await message.reply("Login successful!")
        except Exception as e:
            await message.reply(f"Login failed: {e}")
