"""
plugins/forwarder.py
--------------------
Userbot worker daemon that listens to ALL incoming messages on the authorised
Telegram account and clones them as brand-new posts into TARGET_CHANNEL_ID.

Key design decisions
────────────────────
• No client.forward_messages() / forward API calls are used.
  Instead every message is reconstructed from raw primitives (text, caption,
  media bytes) and re-sent via send_message / send_document etc.
  This completely bypasses "Restrict Saving Content" channel protections.

• An infinite-loop feedback guard prevents the worker from re-forwarding
  messages that originated from TARGET_CHANNEL_ID itself.

• Media is streamed to a BytesIO buffer in memory – no temp files on disk.

• Albums (media groups) are collected via a short debounce window and sent
  in a single send_media_group() call to preserve grouping.

• register() is called by main.py at startup; it only creates and starts the
  worker if a valid active session exists in MongoDB.
"""

import asyncio
import sys
import io
from collections import defaultdict
from typing import Optional, Dict, List

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler
from pyrogram.types import (
    Message,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
)
from pyrogram.errors import FloodWait

from config import cfg
from database import get_active_session

# ── Worker client singleton ───────────────────────────────────────────────────
_worker: Optional[Client] = None

# ── Album debounce: media_group_id → list of Message objects ─────────────────
_album_buffer: Dict[str, List[Message]] = defaultdict(list)
_album_tasks: Dict[str, asyncio.Task] = {}

# ── Debounce delay in seconds for collecting album messages ───────────────────
ALBUM_DEBOUNCE_SECS = 1.5


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _is_feedback_loop(message: Message) -> bool:
    """
    Return True if this message originated from the target channel.
    Prevents an infinite forwarding cycle where our own posts trigger the handler.
    """
    chat_id = message.chat.id if message.chat else None
    # Normalise both sides to absolute int for comparison
    return chat_id is not None and abs(chat_id) == abs(cfg.TARGET_CHANNEL_ID)


async def _download_to_bytesio(worker: Client, message: Message) -> Optional[io.BytesIO]:
    """
    Stream a message's media into an in-memory BytesIO buffer.
    Returns None if the message carries no downloadable media.
    """
    try:
        buf = io.BytesIO()
        await worker.download_media(message, file_name=buf)
        buf.seek(0)
        return buf
    except Exception as exc:
        print(f"[FORWARDER][WARN] Media download failed: {exc}", file=sys.stderr)
        return None


async def _send_with_flood_wait(coro) -> None:
    """
    Execute a send coroutine; if FloodWait is raised wait the required seconds
    then retry once before giving up.
    """
    try:
        await coro
    except FloodWait as fw:
        print(f"[FORWARDER][WARN] FloodWait: sleeping {fw.value}s …", flush=True)
        await asyncio.sleep(fw.value + 1)
        try:
            await coro
        except Exception as exc:
            print(f"[FORWARDER][ERROR] Retry after FloodWait failed: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"[FORWARDER][ERROR] Send failed: {exc}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Single-message cloning logic
# ─────────────────────────────────────────────────────────────────────────────


async def _clone_single(worker: Client, message: Message) -> None:
    """
    Reconstruct and re-send a single (non-album) message to TARGET_CHANNEL_ID.
    """
    target = cfg.TARGET_CHANNEL_ID
    caption = message.caption or ""
    text = message.text or ""

    # ── Text-only ────────────────────────────────────────────────────────────
    if message.text and not message.media:
        await _send_with_flood_wait(
            worker.send_message(chat_id=target, text=text)
        )
        return

    # ── Photo ────────────────────────────────────────────────────────────────
    if message.photo:
        buf = await _download_to_bytesio(worker, message)
        if buf:
            await _send_with_flood_wait(
                worker.send_photo(chat_id=target, photo=buf, caption=caption)
            )
        return

    # ── Video ────────────────────────────────────────────────────────────────
    if message.video:
        buf = await _download_to_bytesio(worker, message)
        if buf:
            buf.name = message.video.file_name or "video.mp4"
            await _send_with_flood_wait(
                worker.send_video(
                    chat_id=target,
                    video=buf,
                    caption=caption,
                    duration=message.video.duration,
                    width=message.video.width,
                    height=message.video.height,
                    supports_streaming=True,
                )
            )
        return

    # ── Audio ────────────────────────────────────────────────────────────────
    if message.audio:
        buf = await _download_to_bytesio(worker, message)
        if buf:
            buf.name = message.audio.file_name or "audio.mp3"
            await _send_with_flood_wait(
                worker.send_audio(
                    chat_id=target,
                    audio=buf,
                    caption=caption,
                    duration=message.audio.duration,
                    performer=message.audio.performer,
                    title=message.audio.title,
                )
            )
        return

    # ── Voice note ───────────────────────────────────────────────────────────
    if message.voice:
        buf = await _download_to_bytesio(worker, message)
        if buf:
            await _send_with_flood_wait(
                worker.send_voice(
                    chat_id=target,
                    voice=buf,
                    caption=caption,
                    duration=message.voice.duration,
                )
            )
        return

    # ── Video note (round video) ──────────────────────────────────────────────
    if message.video_note:
        buf = await _download_to_bytesio(worker, message)
        if buf:
            await _send_with_flood_wait(
                worker.send_video_note(
                    chat_id=target,
                    video_note=buf,
                    duration=message.video_note.duration,
                )
            )
        return

    # ── Sticker ──────────────────────────────────────────────────────────────
    if message.sticker:
        buf = await _download_to_bytesio(worker, message)
        if buf:
            await _send_with_flood_wait(
                worker.send_sticker(chat_id=target, sticker=buf)
            )
        return

    # ── Generic document / file ───────────────────────────────────────────────
    if message.document:
        buf = await _download_to_bytesio(worker, message)
        if buf:
            buf.name = message.document.file_name or "file"
            await _send_with_flood_wait(
                worker.send_document(
                    chat_id=target,
                    document=buf,
                    caption=caption,
                    file_name=message.document.file_name,
                )
            )
        return

    # ── Animation (GIF) ──────────────────────────────────────────────────────
    if message.animation:
        buf = await _download_to_bytesio(worker, message)
        if buf:
            await _send_with_flood_wait(
                worker.send_animation(chat_id=target, animation=buf, caption=caption)
            )
        return

    # ── Poll ─────────────────────────────────────────────────────────────────
    if message.poll and not message.poll.is_anonymous is False:
        try:
            options = [opt.text for opt in message.poll.options]
            await _send_with_flood_wait(
                worker.send_poll(
                    chat_id=target,
                    question=message.poll.question,
                    options=options,
                )
            )
        except Exception as exc:
            print(f"[FORWARDER][WARN] Poll clone failed: {exc}", file=sys.stderr)
        return

    # ── Location ─────────────────────────────────────────────────────────────
    if message.location:
        await _send_with_flood_wait(
            worker.send_location(
                chat_id=target,
                latitude=message.location.latitude,
                longitude=message.location.longitude,
            )
        )
        return

    # ── Contact ──────────────────────────────────────────────────────────────
    if message.contact:
        await _send_with_flood_wait(
            worker.send_contact(
                chat_id=target,
                phone_number=message.contact.phone_number,
                first_name=message.contact.first_name,
                last_name=message.contact.last_name or "",
            )
        )
        return

    # Unsupported type – log and skip
    print(
        f"[FORWARDER][SKIP] Unsupported message type in msg_id={message.id} "
        f"chat={message.chat.id if message.chat else '?'}",
        flush=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Album (media group) cloning logic
# ─────────────────────────────────────────────────────────────────────────────


async def _flush_album(worker: Client, group_id: str) -> None:
    """
    Called after the debounce delay.  Collects all buffered messages for a
    media group, downloads their media, and sends a single media group to the
    target channel.
    """
    await asyncio.sleep(ALBUM_DEBOUNCE_SECS)

    messages = _album_buffer.pop(group_id, [])
    _album_tasks.pop(group_id, None)

    if not messages:
        return

    # Sort by message_id to preserve original order
    messages.sort(key=lambda m: m.id)

    media_list = []
    first = True

    for msg in messages:
        caption = (msg.caption or "") if first else ""
        first = False

        buf = await _download_to_bytesio(worker, msg)
        if not buf:
            continue

        if msg.photo:
            media_list.append(InputMediaPhoto(media=buf, caption=caption))
        elif msg.video:
            buf.name = msg.video.file_name or "video.mp4"
            media_list.append(InputMediaVideo(media=buf, caption=caption))
        elif msg.audio:
            buf.name = msg.audio.file_name or "audio.mp3"
            media_list.append(InputMediaAudio(media=buf, caption=caption))
        elif msg.document:
            buf.name = msg.document.file_name or "file"
            media_list.append(InputMediaDocument(media=buf, caption=caption))

    if media_list:
        await _send_with_flood_wait(
            worker.send_media_group(chat_id=cfg.TARGET_CHANNEL_ID, media=media_list)
        )


# ─────────────────────────────────────────────────────────────────────────────
# Main incoming message handler
# ─────────────────────────────────────────────────────────────────────────────


async def _on_new_message(worker: Client, message: Message) -> None:
    """
    Fired for every incoming message on the authorised user account.
    Routes to album buffer or direct clone depending on media_group_id.
    """
    # ── Feedback loop guard ───────────────────────────────────────────────────
    if _is_feedback_loop(message):
        return

    # ── Ignore outgoing messages from ourselves ───────────────────────────────
    if message.outgoing:
        return

    # ── Album / media group handling ──────────────────────────────────────────
    if message.media_group_id:
        gid = message.media_group_id
        _album_buffer[gid].append(message)

        # Cancel existing debounce task and restart the timer
        if gid in _album_tasks:
            _album_tasks[gid].cancel()

        task = asyncio.create_task(_flush_album(worker, gid))
        _album_tasks[gid] = task
        return

    # ── Single message ────────────────────────────────────────────────────────
    await _clone_single(worker, message)


# ─────────────────────────────────────────────────────────────────────────────
# Worker initialisation
# ─────────────────────────────────────────────────────────────────────────────


async def start_worker(session_string: str) -> None:
    """
    Instantiate and start the userbot worker client from a saved StringSession.
    Registers the NewMessage handler and starts the client in the background.
    """
    global _worker

    _worker = Client(
        name=cfg.WORKER_SESSION_NAME,
        api_id=cfg.API_ID,
        api_hash=cfg.API_HASH,
        session_string=session_string,
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.1.1 x64",
    )

    _worker.add_handler(
        MessageHandler(
            _on_new_message,
            filters=filters.incoming,
        )
    )

    try:
        await _worker.start()
        me = await _worker.get_me()
        print(
            f"[WORKER] Userbot started as @{me.username} (id={me.id}). "
            f"Forwarding to channel {cfg.TARGET_CHANNEL_ID}.",
            flush=True,
        )
    except Exception as exc:
        print(f"[WORKER][ERROR] Failed to start userbot: {exc}", file=sys.stderr)
        _worker = None
        raise


async def stop_worker() -> None:
    """Gracefully stop the userbot worker."""
    global _worker
    if _worker:
        await _worker.stop()
        _worker = None
        print("[WORKER] Userbot stopped.", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Plugin registration – called by main.py dynamic loader
# ─────────────────────────────────────────────────────────────────────────────


async def register(app: Client) -> None:
    """
    Called by main.py after all plugins are loaded.

    Queries MongoDB for an active session and, if found, starts the background
    worker client.  The `app` parameter (Master Bot) is accepted for API
    consistency with other plugins but is not used here.
    """
    session_doc = await get_active_session()

    if not session_doc:
        print(
            "[WORKER] No active session in DB. "
            "Use /login via the bot to authorise your account.",
            flush=True,
        )
        return

    session_string = session_doc.get("session_string")
    if not session_string:
        print("[WORKER][WARN] Session document exists but session_string is empty.", file=sys.stderr)
        return

    await start_worker(session_string)
