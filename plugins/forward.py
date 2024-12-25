import logging
from pyrogram import Client
from database.database import get_user_session, get_forward_rules

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@Client.on_message()
async def forward_messages(client, message):
    user_id = message.from_user.id
    logger.info(f"Processing message from user {user_id}, chat ID: {message.chat.id}")

    forward_rules = get_forward_rules(user_id)
    if not forward_rules:
        logger.info(f"No forward rules found for user {user_id}.")
    
    for rule in forward_rules:
        logger.info(f"Checking forward rule for source chat {rule['source_chat']} and target chat {rule['target_chat']}.")

        if message.chat.id == rule["source_chat"]:
            filters = rule["filters"]
            logger.info(f"Message matches source chat {rule['source_chat']}. Applying filters: {filters}")

            # Apply filters (e.g., photos, videos, documents)
            if filters.get("photo") and message.photo:
                logger.info(f"Forwarding photo from {message.chat.id} to {rule['target_chat']}.")
                await client.forward_messages(
                    rule["target_chat"], message.chat.id, message.id
                )
            elif filters.get("video") and message.video:
                logger.info(f"Forwarding video from {message.chat.id} to {rule['target_chat']}.")
                await client.forward_messages(
                    rule["target_chat"], message.chat.id, message.id
                )
            elif filters.get("document") and message.document:
                logger.info(f"Forwarding document from {message.chat.id} to {rule['target_chat']}.")
                await client.forward_messages(
                    rule["target_chat"], message.chat.id, message.id
                )
            else:
                logger.info(f"Message does not match any of the filters for forwarding.")
        else:
            logger.info(f"Message from chat {message.chat.id} does not match source chat {rule['source_chat']}. Skipping.")
