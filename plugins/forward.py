from pyrogram import Client
from database.database import get_user_session, get_forward_rules

@Client.on_message()
async def forward_messages(client, message):
    for rule in get_forward_rules(message.from_user.id):
        if message.chat.id == rule["source_chat"]:
            filters = rule["filters"]

            # Apply filters (e.g., photos, videos, documents)
            if filters.get("photo") and message.photo:
                await client.forward_messages(
                    rule["target_chat"], message.chat.id, message.id
                )
            elif filters.get("video") and message.video:
                await client.forward_messages(
                    rule["target_chat"], message.chat.id, message.id
                )
            elif filters.get("document") and message.document:
                await client.forward_messages(
                    rule["target_chat"], message.chat.id, message.id
                )
