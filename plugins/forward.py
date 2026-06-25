import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from database.database import get_forward_rules, users_col
from info import API_ID, API_HASH

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Dictionary to keep active userbot sessions alive in memory
active_userbots = {}

async def userbot_forward_handler(client: Client, message):
    """
    This handler listens directly to the user's account traffic via the background worker.
    It instantly intercepts incoming posts and clones them if a rule exists in MongoDB.
    """
    try:
        user_id = client.me.id
        
        # 🔊 VERBOSE LISTENING LOGS
        # This will fire for EVERY single message your account intercepts, showing that the ears are working!
        chat_title = message.chat.title or message.chat.first_name or "Private Chat/Group"
        sender_id = message.from_user.id if message.from_user else "Channel/Bot"
        logger.info(f"👂 [Userbot {user_id}] Hearing traffic in Chat: '{chat_title}' (ID: {message.chat.id}) | From Sender ID: {sender_id}")

        # Offload the database read to a background thread to keep things completely asynchronous
        forward_rules = await asyncio.to_thread(get_forward_rules, user_id)
        if not forward_rules:
            return

        for rule in forward_rules:
            source_chat = rule.get("source_chat")
            target_chat = rule.get("target_chat")
            filters_config = rule.get("filters", {})

            if message.chat.id == target_chat:
                continue

            if message.chat.id == source_chat:
                logger.info(f"⚡ [Userbot {user_id}] Match found! Detected message in target source chat {source_chat}. Processing copy...")

                has_photo = bool(message.photo)
                has_video = bool(message.video)
                has_document = bool(message.document)
                has_text = bool(message.text and not (has_photo or has_video or has_document))

                should_forward = False
                if filters_config.get("photo") and has_photo:
                    should_forward = True
                elif filters_config.get("video") and has_video:
                    should_forward = True
                elif filters_config.get("document") and has_document:
                    should_forward = True
                elif filters_config.get("text") and has_text:
                    should_forward = True
                
                if not any(filters_config.values()):
                    should_forward = True

                if should_forward:
                    try:
                        await message.copy(chat_id=target_chat)
                        logger.info(f"✅ [Userbot {user_id}] Message cleanly duplicated to target: {target_chat}")
                    except Exception as copy_err:
                        logger.error(f"❌ [Userbot {user_id}] Failed to copy content payload: {copy_err}")
                break 
                
    except Exception as e:
        logger.error(f"Error in userbot_forward_handler: {e}")


async def boot_userbots():
    """
    This background daemon wakes up on application boot, reaches into MongoDB,
    and initializes dedicated listening clients for every logged-in user.
    """
    logger.info("🔄 Initializing background Userbot daemons from database...")
    try:
        all_users = await asyncio.to_thread(lambda: list(users_col.find({"session": {"$exists": True, "$ne": None}})))
        
        if not all_users:
            logger.info("⚠️ No active user sessions found in database. Standing by for /login commands.")
            return

        for user_data in all_users:
            user_id = user_data.get("user_id")
            session_string = user_data.get("session")

            if user_id in active_userbots:
                continue

            logger.info(f"🚀 Launching client engine for User ID: {user_id}...")
            
            user_client = Client(
                name=f"worker_{user_id}",
                session_string=session_string,
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True
            )

            user_client.add_handler(MessageHandler(userbot_forward_handler, filters.incoming))

            try:
                await user_client.start()
                user_client.me = await user_client.get_me() 
                active_userbots[user_id] = user_client
                logger.info(f"✅ Background client is listening actively for User: {user_client.me.first_name}")
            except Exception as auth_err:
                logger.error(f"❌ Failed to validate session for {user_id}. It may have been revoked: {auth_err}")

    except Exception as e:
        logger.error(f"Critical exception inside boot_userbots: {e}")
