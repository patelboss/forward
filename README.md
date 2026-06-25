# 🤖 Telegram Forwarder — Modular Self-Healing Userbot

A production-ready Pyrogram-based system that authorises a personal Telegram
account via a bot command and then silently clones **all** incoming messages
(text, media, albums, polls, stickers …) into a private target channel —
bypassing "Restrict Saving Content" protections by reconstructing each
message from raw primitives instead of using the forwarding API.

---

## Architecture

```
MongoDB Atlas  ←──────────────────────────────────────┐
     │  (reads/writes session_string)                   │
     ▼                                                  │
Master Bot  ──/login──►  Ephemeral client               │
(Pyrogram bot)           sends OTP, signs in            │
     │                   exports StringSession ──────────┘
     │                                         (save_session)
     │
     └──► on next boot ──► Userbot Worker (StringSession)
                               │  listens: filters.incoming
                               ▼
                        TARGET_CHANNEL_ID
                        (fresh send_message / send_photo / …)

Koyeb HTTP health-check ← aiohttp server on $PORT
```

---

## Project Structure

```
tg-forwarder/
├── config.py           # Pydantic-Settings ENV loader
├── main.py             # Boot sequence + dynamic plugin loader
├── database.py         # Motor async MongoDB helpers
├── requirements.txt
├── .env.example        # Copy → .env for local dev
├── utils/
│   ├── __init__.py
│   └── health.py       # aiohttp health-check server
└── plugins/
    ├── __init__.py
    ├── login.py        # /login  /logout  /status  conversation FSM
    └── forwarder.py    # Userbot worker daemon + content cloner
```

---

## Quick Start (Local)

```bash
# 1. Clone / copy the project
cd tg-forwarder

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env — fill in API_ID, API_HASH, BOT_TOKEN, MONGO_URI, TARGET_CHANNEL_ID

# 5. Run
python main.py
```

---

## Koyeb Deployment

1. Push the project to a GitHub repository.
2. Create a new **Koyeb Web Service** pointing to the repo.
3. Set **Run command** to `python main.py`.
4. Add all variables from `.env.example` as **Environment Variables** in the
   Koyeb service settings (Koyeb injects `$PORT` automatically — no need to
   set it manually).
5. Deploy.  The aiohttp server binds to `$PORT` and returns `200 OK` on
   `/` and `/health`, satisfying the platform health-check.

---

## Bot Commands

| Command   | Description                                      |
|-----------|--------------------------------------------------|
| `/login`  | Start the interactive phone → OTP → (2FA) flow  |
| `/logout` | Delete the stored session from MongoDB           |
| `/status` | Check whether an active session exists           |

### Login flow

```
User: /login
Bot:  📱 Please send your phone number …
User: +919876543210
Bot:  🔑 OTP sent … Please send the code …
User: 12345   (or "1 2 3 4 5" — spaces are stripped automatically)
Bot:  ✅ Login successful! Restarting …
      [process replaces itself via os.execv]
      [new process boots, loads session from MongoDB, starts worker]
```

If 2-FA is enabled an extra step is inserted:
```
Bot:  🔐 Please send your 2-FA password …
User: mysecretpassword
Bot:  ✅ Login successful! …
```

---

## Extending with New Plugins

1. Create `plugins/my_feature.py`.
2. Implement a `register(app)` function (sync or `async def`).
3. Restart the application.

`main.py` scans `plugins/*.py` on every boot and calls `register(app)` for
each file found.  No changes to `main.py` are needed.

---

## Content Types Supported

| Type          | Cloned as          |
|---------------|--------------------|
| Text          | `send_message`     |
| Photo         | `send_photo`       |
| Video         | `send_video`       |
| Audio         | `send_audio`       |
| Voice note    | `send_voice`       |
| Video note    | `send_video_note`  |
| Document/File | `send_document`    |
| Sticker       | `send_sticker`     |
| Animation/GIF | `send_animation`   |
| Media album   | `send_media_group` |
| Poll          | `send_poll`        |
| Location      | `send_location`    |
| Contact       | `send_contact`     |

---

## Safety Features

- **Feedback-loop guard** — messages originating from `TARGET_CHANNEL_ID`
  are silently ignored to prevent infinite re-forwarding.
- **FloodWait handling** — automatically sleeps the required duration and
  retries once on Telegram rate-limit errors.
- **Album debounce** — a 1.5-second window collects all parts of a media
  group before sending a single `send_media_group` call.
- **Self-restart** — after a successful `/login` the process replaces itself
  via `os.execv` (zero stale state, clean MongoDB reload).
- **Graceful shutdown** — SIGINT / SIGTERM triggers ordered teardown:
  worker → bot → MongoDB → health server.

---

## Environment Variables Reference

| Variable            | Required | Default          | Description                         |
|---------------------|----------|------------------|-------------------------------------|
| `API_ID`            | ✅       | —                | Telegram App API ID (integer)       |
| `API_HASH`          | ✅       | —                | Telegram App API Hash               |
| `BOT_TOKEN`         | ✅       | —                | Master Bot token from @BotFather    |
| `MONGO_URI`         | ✅       | —                | MongoDB Atlas connection string     |
| `TARGET_CHANNEL_ID` | ✅       | —                | Destination channel ID (-100…)      |
| `PORT`              | ❌       | `8080`           | Health-check HTTP port              |
| `MONGO_DB_NAME`     | ❌       | `tg_forwarder`   | MongoDB database name               |
| `MONGO_COLLECTION`  | ❌       | `sessions`       | MongoDB collection name             |
| `WORKER_SESSION_NAME`| ❌      | `userbot_worker` | Pyrogram session file name          |
