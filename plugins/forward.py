import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from database.database import get_forward_rules, users_col
from info import API_ID, API_HASH

# =====================================================================
# ✅ MONKEY PATCH: FIXES PYROGRAM PEER_ID_INVALID FOR CHANNELS
# =====================================================================
from pyrogram import utils
def get_peer_type_patched(peer_id: int) -> str:
    peer_id_str = str(peer_id)
    if not peer_id_str.startswith("-"):
        return "user"
    elif peer_id_str.startswith("-100"):
        return "channel"
    else:
        return "chat"
utils.get_peer_type = get_peer_type_patched
# =====================================================================

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Dictionary to keep active userbot sessions alive in memory
active_userbots = {}
# Keeps track of processed message IDs per channel to avoid duplicates
processed_messages = {}

async def process_and_copy_message(client: Client, message, user_id, target_chat, filters_config):
    """Safely extracts message payloads and replicates them to the target."""
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
            logger.info(f"✅ [Userbot {user_id}] Message duplicated seamlessly to target: {target_chat}")
        except Exception as copy_err:
            logger.error(f"❌ [Userbot {user_id}] Failed to copy payload: {copy_err}")


async def userbot_forward_handler(client: Client, message):
    """Real-time event fallback listener (when gateway works)."""
    try:
        user_id = client.me.id
        chat_title = message.chat.title or message.chat.first_name or "Private Chat"
        logger.info(f"👂 [Userbot {user_id}] Hearing traffic in: '{chat_title}' (ID: {message.chat.id})")

        forward_rules = await asyncio.to_thread(get_forward_rules, user_id)
        if not forward_rules:
            return

        for rule in forward_rules:
            if message.chat.id == rule.get("source_chat") and message.chat.id != rule.get("target_chat"):
                # Mark as processed so polling loop doesn't double-forward
                msg_key = f"{message.chat.id}_{message.id}"
                if msg_key in processed_messages:
                    continue
                processed_messages[msg_key] = True
                
                await process_and_copy_message(client, message, user_id, rule.get("target_chat"), rule.get("filters", {}))
                break
    except Exception as e:
        logger.error(f"Error in live listener: {e}")


async def hyper_poll_sync_loop(user_id, user_client: Client):
    """
    Bulletproof Polling Loop: Actively sweeps channel histories 
    to fetch changes directly, completely bypassing quiet gateway bans.
    """
    logger.info(f"⚡ [Userbot {user_id}] Active Polling Stream Scanner engaged.")
    while user_id in active_userbots:
        try:
            user_rules = await asyncio.to_thread(get_forward_rules, user_id)
            if user_rules:
                for rule in user_rules:
                    src_id = rule.get("source_chat")
                    tgt_id = rule.get("target_chat")
                    filters_config = rule.get("filters", {})

                    try:
                        # Fetch only the last 3 messages to avoid flooding limits
                        async for message in user_client.get_chat_history(chat_id=src_id, limit=3):
                            msg_key = f"{src_id}_{message.id}"
                            
                            # If we haven't seen this message yet, process it!
                            if msg_key not in processed_messages:
                                # Populate cache historical baseline on first sweep so it doesn't dump old logs
                                if src_id not in processed_messages:
                                    processed_messages[msg_key] = True
                                    continue
                                
                                processed_messages[msg_key] = True
                                logger.info(f"🎯 [Scanner Match] Found missing post ID {message.id} in source {src_id}!")
                                await process_and_copy_message(user_client, message, user_id, tgt_id, filters_config)
                        
                        # Set initialization anchor flag for the source chat
                        processed_messages[src_id] = True
                        
                    except Exception as e:
                        logger.debug(f"Sync skip for chat {src_id}: {e}")
            
            # Sweeps history every 10 seconds to keep streaming delivery quick
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            break
        except Exception as loop_err:
            logger.error(f"Error in dynamic loop reader: {loop_err}")
            await asyncio.sleep(5)


async def boot_userbots():
    logger.info("🔄 Initializing background Userbot daemons from database...")
    try:
        all_users = await asyncio.to_thread(lambda: list(users_col.find({"session": {"$exists": True, "$ne": None}})))
        if not all_users:
            logger.info("⚠️ No active user sessions found.")
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
                logger.info(f"✅ Background client active for User: {user_client.me.first_name}")

                # Fire up the hyper poll synchronization task to secure streaming data
                asyncio.create_task(hyper_poll_sync_loop(user_id, user_client))

            except Exception as auth_err:
                logger.error(f"❌ Failed to validate session for {user_id}: {auth_err}")

    except Exception as e:
        logger.error(f"Critical exception inside boot_userbots: {e}")
