import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from database.database import save_user_session, get_user_session

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Shared Pyrogram client for sending OTPs

@Client.on_message(filters.command("login"))
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
        # Step 1: Ask for the phone number
        logger.info(f"Requesting phone number from user {user_id}.")
        await message.reply("<b>Enter your phone number:</b>", parse_mode=ParseMode.HTML)
        phone = await client.ask(message.chat.id, "<b>Please provide your phone number:</b>", parse_mode=ParseMode.HTML)
        logger.info(f"Received phone number from user {user_id}: {phone.text}")

        # Create a temporary client for user authentication
        user_client = Client(":memory:")

        async with user_client:
            # Send the OTP to the provided phone number
            logger.info(f"Sending OTP to phone number {phone.text}.")
            sent = await user_client.send_code(phone.text)
            await message.reply("<b>Enter the code you received (e.g., 1 2 3 4 5):</b>", parse_mode=ParseMode.HTML)

            # Step 2: Wait for OTP input with spaces
            otp = await client.ask(message.chat.id, "<b>Enter the OTP code:</b>", parse_mode=ParseMode.HTML)
            otp = otp.text.replace(" ", "")  # Remove spaces for verification
            logger.info(f"Received OTP from user {user_id}: {otp}")

            # Step 3: Sign in using the OTP
            logger.info(f"Attempting to sign in user {user_id} with OTP.")
            await user_client.sign_in(phone.text, sent.phone_code_hash, otp)

            # Step 4: Ask for a 2-step verification PIN if enabled
            if user_client.is_authorized() and user_client.me.two_step_verification_enabled:
                logger.info(f"Two-step verification enabled for user {user_id}. Requesting PIN.")
                await message.reply("<b>Two-step verification is enabled. Enter your PIN:</b>", parse_mode=ParseMode.HTML)
                pin = await client.ask(message.chat.id, "<b>Please enter your 2-step PIN:</b>", parse_mode=ParseMode.HTML)
                await user_client.check_password(pin.text)
                logger.info(f"Two-step verification successful for user {user_id}.")
                await message.reply("<b>Two-step verification successful!</b>", parse_mode=ParseMode.HTML)

            # Step 5: Save session and complete the login
            logger.info(f"Saving session for user {user_id}.")
            save_user_session(user_id, user_client.export_session_string())
            logger.info(f"Login successful for user {user_id}.")
            await message.reply("<b>Login successful!</b>", parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Login failed for user {user_id}: {e}")
        await message.reply(f"<b>Login failed:</b> {e}", parse_mode=ParseMode.HTML)

