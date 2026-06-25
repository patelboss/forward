import asyncio
import logging
import time
from bson.objectid import ObjectId

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.database import (
    get_user_session,
    save_forward_rule,
    get_forward_rules,
    rules_col,
)
from info import API_ID, API_HASH

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 15 * 60

# State track map keeping states split per user session.
# Format:
# {
#   user_id: {
#       "sources": [id1, id2, ...],
#       "target": int | None,
#       "filters": {"text": bool, "photo": bool, "video": bool, "document": bool},
#       "chats_cache": {chat_id: title},
#       "created_at": float
#   }
# }
setup_state = {}


async def _safe_disconnect(app: Client) -> None:
    try:
        if app.is_connected:
            await app.disconnect()
    except Exception:
        logger.exception("Failed to disconnect temporary client cleanly.")


def _now() -> float:
    return time.time()


def _cleanup_expired_state(user_id: int) -> None:
    state = setup_state.get(user_id)
    if not state:
        return
    created_at = state.get("created_at", 0.0)
    if _now() - created_at > STATE_TTL_SECONDS:
        setup_state.pop(user_id, None)


def _prune_all_expired_states() -> None:
    expired = []
    current = _now()
    for user_id, state in setup_state.items():
        if current - state.get("created_at", 0.0) > STATE_TTL_SECONDS:
            expired.append(user_id)
    for user_id in expired:
        setup_state.pop(user_id, None)


def _get_active_state(user_id: int):
    _cleanup_expired_state(user_id)
    return setup_state.get(user_id)


def _parse_chat_id(callback_data: str, prefix: str) -> int:
    try:
        if not callback_data.startswith(prefix):
            raise ValueError("Invalid prefix")
        return int(callback_data.rsplit("_", 1)[1])
    except (IndexError, ValueError) as err:
        raise ValueError(f"Invalid callback data ID mapping payload: {callback_data}") from err


async def _fetch_all_dialogs_dict(session_string: str, user_id: int) -> dict[int, str]:
    """Return chat names indexed by chat ID."""
    app = Client(
        name=f"temp_d_fetch_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )
    chats_map: dict[int, str] = {}
    await app.connect()
    try:
        async for dialog in app.get_dialogs(limit=50):
            title = dialog.chat.title or dialog.chat.first_name or "Unknown Chat"
            chats_map[dialog.chat.id] = title[:30]
    finally:
        await asyncio.sleep(1)
        await _safe_disconnect(app)
    return chats_map


def _build_source_keyboard(chats_map: dict[int, str], selected_sources: list[int]) -> InlineKeyboardMarkup:
    buttons = []
    for cid, title in chats_map.items():
        prefix_tag = "✅ " if cid in selected_sources else "📁 "
        buttons.append([InlineKeyboardButton(f"{prefix_tag}{title}", callback_data=f"src_toggle_{cid}")])
    buttons.append([InlineKeyboardButton("➡️ NEXT STEP", callback_data="src_lock_next")])
    return InlineKeyboardMarkup(buttons)


def _build_target_keyboard(chats_map: dict[int, str], selected_sources: list[int]) -> InlineKeyboardMarkup:
    buttons = []
    for cid, title in chats_map.items():
        if cid in selected_sources:
            continue
        buttons.append([InlineKeyboardButton(f"🎯 {title}", callback_data=f"tgt_select_{cid}")])
    return InlineKeyboardMarkup(buttons)


async def _render_filter_menu(message, user_id: int) -> None:
    state = _get_active_state(user_id)
    if not state:
        await message.edit_text("Session expired. Please issue /set_forwarding again.", parse_mode=ParseMode.HTML)
        return

    current_filters = state["filters"]

    buttons = [
        [InlineKeyboardButton(f"{'🟢 ON' if current_filters['text'] else '🔴 OFF'} | Copy Text Messages", callback_data="flt_toggle_text")],
        [InlineKeyboardButton(f"{'🟢 ON' if current_filters['photo'] else '🔴 OFF'} | Copy Photo Media", callback_data="flt_toggle_photo")],
        [InlineKeyboardButton(f"{'🟢 ON' if current_filters['video'] else '🔴 OFF'} | Copy Video Content", callback_data="flt_toggle_video")],
        [InlineKeyboardButton(f"{'🟢 ON' if current_filters['document'] else '🔴 OFF'} | Copy Document Files", callback_data="flt_toggle_document")],
        [InlineKeyboardButton("💾 SAVE RULES", callback_data="flt_save_commit")],
    ]

    await message.edit_text(
        "<b>Step 3: Modify specific file filter configuration values simultaneously:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


# =====================================================================
# STEP 1: INITIALIZE MULTI-SOURCE SELECTOR INTERFACE
# =====================================================================
@Client.on_message(filters.command("set_forwarding") & filters.private)
async def set_forwarding(client, message):
    user_id = message.from_user.id
    logger.info("User %s initiated /set_forwarding.", user_id)

    _prune_all_expired_states()

    session_string = get_user_session(user_id)
    if not session_string:
        await message.reply(
            "<b>❌ You are not logged in.</b>\n\nPlease log in first using /login.",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await message.reply("🔄 Initializing dynamic workspace wizard...", parse_mode=ParseMode.HTML)

    try:
        chats_map = await _fetch_all_dialogs_dict(session_string, user_id)
        if not chats_map:
            await status_msg.edit_text("Could not discover any chats available under this profile account.")
            return

        setup_state[user_id] = {
            "sources": [],
            "target": None,
            "filters": {"text": True, "photo": True, "video": True, "document": True},
            "chats_cache": chats_map,
            "created_at": _now(),
        }

        await status_msg.edit_text(
            "<b>Step 1: Select all SOURCE chats (you can choose multiple simultaneously):</b>",
            reply_markup=_build_source_keyboard(chats_map, []),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.exception("Failed setup entry for user %s.", user_id)
        await status_msg.edit_text(f"Error initializing wizard: {e}")


# =====================================================================
# STEP 1 INTERACTIVE: HANDLE TOGGLINGS AND NEXT ACTION
# =====================================================================
@Client.on_callback_query(filters.regex(r"^src_toggle_"))
async def src_toggle_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired. Please issue /set_forwarding again.", show_alert=True)
        return

    try:
        selected_cid = _parse_chat_id(callback_query.data, "src_toggle_")
    except ValueError:
        await callback_query.answer("Invalid source selection.", show_alert=True)
        return

    current_sources = state["sources"]

    if selected_cid in current_sources:
        current_sources.remove(selected_cid)
    else:
        current_sources.append(selected_cid)

    try:
        await callback_query.message.edit_reply_markup(
            reply_markup=_build_source_keyboard(state["chats_cache"], current_sources)
        )
        await callback_query.answer()
    except Exception as e:
        logger.error("Failed to refresh source selection UI: %s", e)
        await callback_query.answer("Could not update selection UI.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^src_lock_next$"))
async def src_lock_next_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired. Please issue /set_forwarding again.", show_alert=True)
        return

    if not state["sources"]:
        await callback_query.answer("⚠️ Please select at least one source channel before continuing!", show_alert=True)
        return

    target_keyboard = _build_target_keyboard(state["chats_cache"], state["sources"])
    if not target_keyboard.inline_keyboard:
        await callback_query.answer(
            "⚠️ No valid target chat available. Please restart and choose a different source set.",
            show_alert=True,
        )
        return

    try:
        await callback_query.message.edit_text(
            "<b>Step 2: Select the single TARGET chat destination where copies will land:</b>",
            reply_markup=target_keyboard,
            parse_mode=ParseMode.HTML,
        )
        await callback_query.answer()
    except Exception as e:
        logger.error("Failed to render target selection UI: %s", e)
        await callback_query.answer("Could not load target selection.", show_alert=True)


# =====================================================================
# STEP 2 INTERACTIVE: CAPTURE TARGET CHAT DESIGNATION
# =====================================================================
@Client.on_callback_query(filters.regex(r"^tgt_select_"))
async def tgt_select_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired.", show_alert=True)
        return

    try:
        target_cid = _parse_chat_id(callback_query.data, "tgt_select_")
    except ValueError:
        await callback_query.answer("Invalid target selection.", show_alert=True)
        return

    if target_cid in state["sources"]:
        await callback_query.answer("Target chat cannot be one of the source chats.", show_alert=True)
        return

    state["target"] = target_cid

    try:
        await callback_query.answer()
        await _render_filter_menu(callback_query.message, user_id)
    except Exception as e:
        logger.error("Failed to render filter menu: %s", e)
        await callback_query.answer("Could not load filter menu.", show_alert=True)


# =====================================================================
# STEP 3 INTERACTIVE: FILTER TOGGLES
# =====================================================================
@Client.on_callback_query(filters.regex(r"^flt_toggle_"))
async def flt_toggle_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired.", show_alert=True)
        return

    filter_key = callback_query.data.split("_")[-1]
    if filter_key not in state["filters"]:
        await callback_query.answer("Invalid filter key.", show_alert=True)
        return

    state["filters"][filter_key] = not state["filters"][filter_key]

    try:
        await callback_query.answer()
        await _render_filter_menu(callback_query.message, user_id)
    except Exception as e:
        logger.error("Failed to refresh filter menu: %s", e)
        await callback_query.answer("Could not update filter menu.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^flt_save_commit$"))
async def flt_save_commit_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired.", show_alert=True)
        return

    sources = state["sources"]
    target = state.get("target")
    final_filters = state["filters"]

    if not sources:
        await callback_query.answer("Please select at least one source chat.", show_alert=True)
        return

    if target is None:
        await callback_query.answer("Please select a target chat first.", show_alert=True)
        return

    if target in sources:
        await callback_query.answer("Target chat cannot be one of the source chats.", show_alert=True)
        return

    if not any(final_filters.values()):
        await callback_query.answer("At least one filter must remain ON.", show_alert=True)
        return

    session_string = get_user_session(user_id)
    if not session_string:
        setup_state.pop(user_id, None)
        await callback_query.answer("Session expired. Please log in again.", show_alert=True)
        return

    try:
        # Avoid duplicate rules for the same source-target pair.
        for src in sources:
            try:
                rules_col.delete_many(
                    {
                        "user_id": user_id,
                        "source_chat": src,
                        "target_chat": target,
                    }
                )
            except Exception:
                logger.exception("Failed to clean existing duplicate rules for user %s.", user_id)

            save_forward_rule(user_id, src, target, final_filters)

        setup_state.pop(user_id, None)

        await callback_query.message.edit_text(
            f"<b>✅ Forwarding Rules Configured Successfully!</b>\n\n"
            f"<b>Total Sources Synchronized:</b> <code>{len(sources)} chats</code>\n"
            f"<b>Target Mapping Destination:</b> <code>{target}</code>\n\n"
            f"Your forwarding worker can now load these rules from the database.",
            parse_mode=ParseMode.HTML,
        )
        await callback_query.answer()
    except Exception as e:
        logger.exception("Failed saving forwarding rules for user %s.", user_id)
        await callback_query.answer("Could not save rules.", show_alert=True)
        try:
            await callback_query.message.edit_text(
                f"Error saving forwarding rules: {e}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


# =====================================================================
# DATABASE MANAGEMENT: REMOVE RULES
# =====================================================================
@Client.on_message(filters.command("remove_forwarding") & filters.private)
async def remove_forwarding_cmd(client, message):
    user_id = message.from_user.id
    _prune_all_expired_states()

    rules = get_forward_rules(user_id)
    if not rules:
        await message.reply("No active configuration forward rules discovered in database storage.")
        return

    buttons = []
    for rule in rules:
        src = rule.get("source_chat")
        tgt = rule.get("target_chat")
        rule_id = rule.get("_id")
        label = f"🗑️ Remove: {src} ➡️ {tgt}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"del_rule_{rule_id}")])

    await message.reply(
        "<b>Select an operational pipeline rule below to permanently drop it from database storage:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^del_rule_"))
async def del_rule_handler(client, callback_query):
    user_id = callback_query.from_user.id

    try:
        rule_id_str = callback_query.data.split("del_rule_", 1)[1]
        rule_object_id = ObjectId(rule_id_str)
    except Exception:
        await callback_query.answer("Invalid rule reference.", show_alert=True)
        return

    try:
        result = rules_col.delete_one({"_id": rule_object_id, "user_id": user_id})
        if result.deleted_count == 0:
            await callback_query.answer("Rule not found or not owned by you.", show_alert=True)
            return

        await callback_query.message.edit_text(
            "<b>🗑️ Forwarding configuration entry purged cleanly from MongoDB data records.</b>\n\n"
            "Please restart the background worker if your deployment requires a reload.",
            parse_mode=ParseMode.HTML,
        )
        await callback_query.answer()
    except Exception as e:
        logger.error("Failed processing delete command for user %s: %s", user_id, e, exc_info=True)
        await callback_query.answer("Error deleting rule.", show_alert=True)
        try:
            await callback_query.message.edit_text(
                f"Error handling document deletion profile mapping: {e}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
