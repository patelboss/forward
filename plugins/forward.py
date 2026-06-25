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
    This handler is attached to the USER'S client, not the Master Bot.
    It listens to the user's incoming chats and clones them based on their rules.
    """
    try:
        user_id = client.me.id
        forward_rules = get_forward_rules(user_id)
        
        if not forward_rules:
            return

        for rule in forward_rules:
            source_chat = rule.get("source_chat")
            target_chat = rule.get("target_chat")
            filters_config = rule.get("filters", {})

            # Prevent infinite forwarding loops if the user interacts in the target chat
            if message.chat.id == target_chat:
                continue

            if message.chat.id == source_chat:
                logger.info(f"[Userbot {user_id}] New message detected in source: {source_chat}. Applying filters...")

                # Filter checks
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
                
                # Fallback: if no specific filters matched but the dictionary was empty/missing, forward anyway
                if not any(filters_config.values()):
                    should_forward = True

                if should_forward:
                    logger.info(f"[Userbot {user_id}] Cloning message to target: {target_chat}...")
                    try:
                        # ✅ THE MAGIC BYPASS: 
                        # message.copy() strips the "Forwarded from" tag and downloads/re-uploads 
                        # the media natively if the source channel is restricted!
                        await message.copy(chat_id=target_chat)
                        logger.info(f"✅ [Userbot {user_id}] Successfully copied message to {target_chat}.")
                    except Exception as copy_err:
                        logger.error(f"❌ [Userbot {user_id}] Failed to copy message: {copy_err}")
                else:
                    logger.info(f"[Userbot {user_id}] Message ignored due to filter configuration.")
                    
    except Exception as e:
        logger.error(f"Error in userbot_forward_handler: {e}")


async def boot_userbots():
    """
    This background task fetches all saved user string sessions from MongoDB
    and silently boots them up as background workers alongside the Master Bot.
    """
    logger.info("🔄 Initializing background Userbot daemons from database...")
    try:
        # Fetch all user sessions directly from the collection
        all_users = list(users_col.find({"session": {"$exists": True, "$ne": None}}))
        
        if not all_users:
            logger.info("⚠️ No active user sessions found in the database. Workers standing by.")
            return

        for user_data in all_users:
            user_id = user_data.get("user_id")
            session_string = user_data.get("session")

            if user_id in active_userbots:
                continue

            logger.info(f"🚀 Booting background worker for User ID: {user_id}...")
            
            # Create a localized memory client for this specific user
            user_client = Client(
                name=f"worker_{user_id}",
                session_string=session_string,
                api_id=API_ID,
                api_hash=API_HASH,
                in_memory=True
            )

            # Attach the copying handler to this user's incoming messages
            user_client.add_handler(MessageHandler(userbot_forward_handler, filters.incoming))

            try:
                await user_client.start()
                # Cache the user's ID within the client object for the handler to read
                user_client.me = await user_client.get_me() 
                active_userbots[user_id] = user_client
                logger.info(f"✅ Background worker active for User: {user_client.me.first_name}")
            except Exception as auth_err:
                logger.error(f"❌ Failed to start worker for {user_id}. Session may be expired: {auth_err}")

    except Exception as e:
        logger.error(f"Critical error during Userbot boot sequence: {e}")

# =====================================================================
# DYNAMIC BOOTLOADER HOOK
# When Pyrogram dynamically imports this plugin file on startup, 
# it schedules the Userbot Boot sequence into the active asyncio loop.
# =====================================================================
try:
    loop = asyncio.get_running_loop()
    loop.create_task(boot_userbots())
except RuntimeError:
    pass # Loop isn't running yet (e.g., during initial script parsing)
