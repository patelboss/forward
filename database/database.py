from pymongo import MongoClient
from config import DATABASE_URI, DATABASE_NAME

# Initialize MongoDB client
client = MongoClient(DATABASE_URI)
db = client[DATABASE_NAME]

# Collections
users_col = db["users"]
rules_col = db["forward_rules"]

def save_user_session(user_id, session):
    """Save or update a user's session."""
    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"session": session}},
        upsert=True
    )

def get_user_session(user_id):
    """Retrieve a user's session."""
    user = users_col.find_one({"user_id": user_id})
    return user["session"] if user else None

def save_forward_rule(user_id, source_chat, target_chat, filters):
    """Save a forwarding rule."""
    rules_col.update_one(
        {"user_id": user_id, "source_chat": source_chat, "target_chat": target_chat},
        {"$set": {"filters": filters}},
        upsert=True
    )

def get_forward_rules(user_id):
    """Retrieve all forwarding rules for a user."""
    return list(rules_col.find({"user_id": user_id}))

def get_all_chats():
    """Fetch all unique chats."""
    return list(rules_col.distinct("source_chat"))
