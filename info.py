import re
from os import environ

#SESSION = environ.get('SESSION', 'Media_search')
API_ID = int(environ['API_ID'])
API_HASH = environ['API_HASH']
BOT_TOKEN = environ['BOT_TOKEN']
DATABASE_URI = environ.get('DATABASE_URI', "")
DATABASE_NAME = environ.get('DATABASE_NAME', "LazyDeveloper")
COLLECTION_NAME = environ.get('COLLECTION_NAME', 'Telegram_files')
