import re
from os import environ

# Fetch and log API_ID, API_HASH, and BOT_TOKEN
try:
    API_ID = int(environ['API_ID'])
    print(f"API_ID: {API_ID}")
except KeyError:
    print("Error: API_ID not found in environment variables")

try:
    API_HASH = environ['API_HASH']
    print(f"API_HASH: {API_HASH}")
except KeyError:
    print("Error: API_HASH not found in environment variables")

try:
    BOT_TOKEN = environ['BOT_TOKEN']
    print(f"BOT_TOKEN: {BOT_TOKEN}")
except KeyError:
    print("Error: BOT_TOKEN not found in environment variables")

# Database URI and related info
DATABASE_URI = environ.get('DATABASE_URI', "")
if DATABASE_URI:
    print(f"DATABASE_URI: {DATABASE_URI}")
else:
    print("Warning: DATABASE_URI not found, using empty string.")

DATABASE_NAME = environ.get('DATABASE_NAME', "LazyDeveloper")
print(f"DATABASE_NAME: {DATABASE_NAME}")

COLLECTION_NAME = environ.get('COLLECTION_NAME', 'Telegram_files')
print(f"COLLECTION_NAME: {COLLECTION_NAME}")
# Log the session (uncomment if you want to use it)
BOT_SESSION = environ.get('BOT_SESSION', 'Media_search')
print(f"SESSION: {BOT_SESSION}")

import logging

# Create a function to initialize the logger
def LOGGER(name: str = None):
    """
    Returns a configured logger instance.
    :param name: Name of the logger (usually the module's __name__).
    :return: Logger instance.
    """
    logger = logging.getLogger(name or "BOT")  # Use the provided name or default to 'BOT'
    
    if not logger.handlers:  # Prevent adding multiple handlers
        # Create a console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # Define a log format
        formatter = logging.Formatter(
            "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s", 
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(formatter)

        # Add the console handler to the logger
        logger.addHandler(console_handler)
    
    # Set the overall logging level
    logger.setLevel(logging.INFO)
    
    return logger

