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
ITEMS_PER_PAGE = 10

# State format:
# {
#   user_id: {
#       "action_mode": "new" | "modify",
#       "sources": [id1, id2, ...],
#       "target": int | None,
#       "filters": {"text": bool, "photo": bool, "video": bool, "document": bool},
#       "chats_cache": [(chat_id, title), ...],
#       "current_page": int,
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
    if _now() - state.get("created_at", 0.0) > STATE_TTL_SECONDS:
        setup_state.pop(user_id, None)


def _prune_all_expired_states() -> None:
    current = _now()
    expired = [
        uid for uid, st in setup_state.items()
        if current - st.get("created_at", 0.0) > STATE_TTL_SECONDS
    ]
    for uid in expired:
        setup_state.pop(uid, None)


def _get_active_state(user_id: int):
    _cleanup_expired_state(user_id)
    return setup_state.get(user_id)


def _parse_chat_id(callback_data: str, prefix: str) -> int:
    try:
        if not callback_data.startswith(prefix):
            raise ValueError("Invalid prefix")
        return int(callback_data.rsplit("_", 1)[1])
    except (IndexError, ValueError) as err:
        raise ValueError(f"Invalid callback payload mapping: {callback_data}") from err


def _parse_page_index(callback_data: str, prefix: str) -> int:
    try:
        if not callback_data.startswith(prefix):
            raise ValueError("Invalid prefix")
        return int(callback_data.rsplit("_", 1)[1])
    except (IndexError, ValueError) as err:
        raise ValueError(f"Invalid page payload: {callback_data}") from err


async def _fetch_all_dialogs_list(session_string: str, user_id: int) -> list[tuple[int, str]]:
    """Return a list of tuples containing (chat_id, chat_title) ordered by recent interaction."""
    app = Client(
        name=f"temp_d_fetch_{user_id}",
        session_string=session_string,
        api_id=API_ID,
        api_hash=API_HASH,
        in_memory=True,
    )
    chats_list: list[tuple[int, str]] = []
    await app.connect()
    try:
        async for dialog in app.get_dialogs(limit=200):
            title = dialog.chat.title or dialog.chat.first_name or "Unknown Chat"
            chats_list.append((dialog.chat.id, title[:25]))
    finally:
        await asyncio.sleep(0.5)
        await _safe_disconnect(app)
    return chats_list


def _total_pages(item_count: int) -> int:
    return max(1, (item_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)


def _build_paginated_source_keyboard(
    chats_cache: list[tuple[int, str]],
    selected_sources: list[int],
    page: int,
) -> InlineKeyboardMarkup:
    buttons = []
    total_items = len(chats_cache)
    total_pages = _total_pages(total_items)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = chats_cache[start_idx:end_idx]

    for cid, title in page_items:
        prefix_tag = "✅ " if cid in selected_sources else "📁 "
        buttons.append([InlineKeyboardButton(f"{prefix_tag}{title}", callback_data=f"src_toggle_{cid}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"src_page_{page - 1}"))
    else:
        nav_row.append(InlineKeyboardButton("❌ First", callback_data="none_alert"))

    nav_row.append(InlineKeyboardButton(f"Done ({len(selected_sources)})", callback_data="src_lock_next"))

    if end_idx < total_items:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"src_page_{page + 1}"))
    else:
        nav_row.append(InlineKeyboardButton("❌ Last", callback_data="none_alert"))

    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_back")])
    return InlineKeyboardMarkup(buttons)


def _build_paginated_target_keyboard(
    chats_cache: list[tuple[int, str]],
    selected_sources: list[int],
    page: int,
) -> InlineKeyboardMarkup:
    buttons = []

    valid_targets = [(cid, title) for cid, title in chats_cache if cid not in selected_sources]
    total_items = len(valid_targets)
    total_pages = _total_pages(total_items)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = valid_targets[start_idx:end_idx]

    for cid, title in page_items:
        buttons.append([InlineKeyboardButton(f"🎯 {title}", callback_data=f"tgt_select_{cid}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"tgt_page_{page - 1}"))
    else:
        nav_row.append(InlineKeyboardButton("❌ First", callback_data="none_alert"))

    nav_row.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="none_alert"))

    if end_idx < total_items:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"tgt_page_{page + 1}"))
    else:
        nav_row.append(InlineKeyboardButton("❌ Last", callback_data="none_alert"))

    buttons.append(nav_row)
    buttons.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_back")])
    return InlineKeyboardMarkup(buttons)


async def _render_filter_menu(message, user_id: int) -> None:
    state = _get_active_state(user_id)
    if not state:
        await message.edit_text("Session expired. Please issue /settings again.", parse_mode=ParseMode.HTML)
        return

    current_filters = state["filters"]
    buttons = [
        [InlineKeyboardButton(f"{'🟢 ON' if current_filters['text'] else '🔴 OFF'} | Text Messages", callback_data="flt_toggle_text")],
        [InlineKeyboardButton(f"{'🟢 ON' if current_filters['photo'] else '🔴 OFF'} | Photo Media", callback_data="flt_toggle_photo")],
        [InlineKeyboardButton(f"{'🟢 ON' if current_filters['video'] else '🔴 OFF'} | Video Content", callback_data="flt_toggle_video")],
        [InlineKeyboardButton(f"{'🟢 ON' if current_filters['document'] else '🔴 OFF'} | Document Files", callback_data="flt_toggle_document")],
        [InlineKeyboardButton("💾 SAVE RULES & DEPLOY", callback_data="flt_save_commit")],
    ]
    await message.edit_text(
        "<b>Step 3: Modify specific routing file extension filters:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )


def _build_main_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 New Configuration Pipeline", callback_data="menu_new_config")],
        [InlineKeyboardButton("⚙️ Modify Existing Filters", callback_data="menu_modify_rules")],
        [InlineKeyboardButton("🗑️ Remove Active Rules", callback_data="menu_remove_rules")],
    ])


# =====================================================================
# SYSTEM COMMAND: INITIALIZE CENTRAL SETTINGS PANEL
# =====================================================================
@Client.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client, message):
    user_id = message.from_user.id
    _prune_all_expired_states()

    session_string = get_user_session(user_id)
    if not session_string:
        await message.reply(
            "<b>❌ Authentication Required</b>\n\nPlease log in first using /login.",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.reply(
        "<b>🎛️ Control Panel Hub</b>\nSelect an operation to modify automated tracking configurations:",
        reply_markup=_build_main_settings_menu(),
        parse_mode=ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^menu_back$"))
async def menu_back_handler(client, callback_query):
    user_id = callback_query.from_user.id
    setup_state.pop(user_id, None)
    await callback_query.message.edit_text(
        "<b>🎛️ Control Panel Hub</b>\nSelect an operation to modify automated tracking configurations:",
        reply_markup=_build_main_settings_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback_query.answer()


# =====================================================================
# FLOW A: NEW CONFIGURATION INTERACTIVE
# =====================================================================
@Client.on_callback_query(filters.regex(r"^menu_new_config$"))
async def menu_new_config_handler(client, callback_query):
    user_id = callback_query.from_user.id
    session_string = get_user_session(user_id)

    if not session_string:
        await callback_query.answer("Please log in first using /login.", show_alert=True)
        return

    await callback_query.message.edit_text("🔄 Scanning session dialogs workspace, please wait...")
    await callback_query.answer()

    try:
        chats_list = await _fetch_all_dialogs_list(session_string, user_id)
        if not chats_list:
            await callback_query.message.edit_text("No conversational workspaces discoverable in account history.")
            return

        setup_state[user_id] = {
            "action_mode": "new",
            "sources": [],
            "target": None,
            "filters": {"text": True, "photo": True, "video": True, "document": True},
            "chats_cache": chats_list,
            "current_page": 0,
            "created_at": _now(),
        }

        await callback_query.message.edit_text(
            "<b>Step 1: Select SOURCE chats (You can select multiple entries across pages):</b>",
            reply_markup=_build_paginated_source_keyboard(chats_list, [], 0),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.exception("Wizard startup failure on profile %s", user_id)
        await callback_query.message.edit_text(f"Initialization crashed profile mapping structure: {e}")


@Client.on_callback_query(filters.regex(r"^src_page_"))
async def src_page_navigation_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired.", show_alert=True)
        return

    try:
        target_page = _parse_page_index(callback_query.data, "src_page_")
    except ValueError:
        await callback_query.answer("Invalid page navigation.", show_alert=True)
        return

    state["current_page"] = target_page
    await callback_query.message.edit_reply_markup(
        reply_markup=_build_paginated_source_keyboard(state["chats_cache"], state["sources"], target_page)
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^src_toggle_"))
async def src_toggle_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired.", show_alert=True)
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

    await callback_query.message.edit_reply_markup(
        reply_markup=_build_paginated_source_keyboard(
            state["chats_cache"],
            current_sources,
            state["current_page"],
        )
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^src_lock_next$"))
async def src_lock_next_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state or not state["sources"]:
        await callback_query.answer("⚠️ Select at least one source element before moving forward!", show_alert=True)
        return

    valid_targets = [(cid, title) for cid, title in state["chats_cache"] if cid not in state["sources"]]
    if not valid_targets:
        await callback_query.answer("⚠️ No valid target chat available.", show_alert=True)
        return

    state["current_page"] = 0
    target_keyboard = _build_paginated_target_keyboard(state["chats_cache"], state["sources"], 0)

    await callback_query.message.edit_text(
        "<b>Step 2: Assign destination TARGET chat where forward captures map into:</b>",
        reply_markup=target_keyboard,
        parse_mode=ParseMode.HTML,
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^tgt_page_"))
async def tgt_page_navigation_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired.", show_alert=True)
        return

    try:
        target_page = _parse_page_index(callback_query.data, "tgt_page_")
    except ValueError:
        await callback_query.answer("Invalid page navigation.", show_alert=True)
        return

    state["current_page"] = target_page
    await callback_query.message.edit_reply_markup(
        reply_markup=_build_paginated_target_keyboard(state["chats_cache"], state["sources"], target_page)
    )
    await callback_query.answer()


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

    await callback_query.answer()
    await _render_filter_menu(callback_query.message, user_id)


# =====================================================================
# FILTER PROCESSING CONTROLS & COMMITTING DATA TO MONGO
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

    await callback_query.answer()
    await _render_filter_menu(callback_query.message, user_id)


@Client.on_callback_query(filters.regex(r"^flt_save_commit$"))
async def flt_save_commit_handler(client, callback_query):
    user_id = callback_query.from_user.id
    state = _get_active_state(user_id)
    if not state:
        await callback_query.answer("Session expired.", show_alert=True)
        return

    sources = state["sources"]
    target = state["target"]
    final_filters = state["filters"]

    if not sources:
        await callback_query.answer("⚠️ Please select at least one source.", show_alert=True)
        return

    if target is None:
        await callback_query.answer("⚠️ Please select a target chat.", show_alert=True)
        return

    if target in sources:
        await callback_query.answer("⚠️ Target chat cannot be one of the source chats.", show_alert=True)
        return

    if not any(final_filters.values()):
        await callback_query.answer("⚠️ You must keep at least one filter enabled!", show_alert=True)
        return

    try:
        for src in sources:
            rules_col.delete_many({"user_id": user_id, "source_chat": src, "target_chat": target})
            save_forward_rule(user_id, src, target, final_filters)

        setup_state.pop(user_id, None)
        await callback_query.message.edit_text(
            f"<b>✅ Configuration Database Updated Successfully!</b>\n\n"
            f"🔗 Total Subscribed Streams: <code>{len(sources)} chats</code>\n"
            f"🎯 Sink Pipeline Endpoint: <code>{target}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Back to Settings", callback_data="menu_back")]]),
            parse_mode=ParseMode.HTML,
        )
        await callback_query.answer()
    except Exception as e:
        logger.exception("Failed database transactions for user record %s", user_id)
        await callback_query.answer("Failed writing updates.", show_alert=True)
        try:
            await callback_query.message.edit_text(f"Error saving forwarding rules: {e}", parse_mode=ParseMode.HTML)
        except Exception:
            pass


# =====================================================================
# FLOW B & C: MODIFY FILTERS & REMOVE ACTIVE PIPELINES
# =====================================================================
@Client.on_callback_query(filters.regex(r"^menu_modify_rules$"))
async def menu_modify_rules_handler(client, callback_query):
    user_id = callback_query.from_user.id
    rules = get_forward_rules(user_id)
    if not rules:
        await callback_query.answer("No active pipelines configured to modify.", show_alert=True)
        return

    buttons = []
    for r in rules:
        buttons.append([
            InlineKeyboardButton(
                f"⚙️ Tune: {r.get('source_chat')} ➡️ {r.get('target_chat')}",
                callback_data=f"mod_select_{r.get('_id')}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")])

    await callback_query.message.edit_text(
        "<b>Select an active pipeline configuration instance to tune filters:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^mod_select_"))
async def mod_select_handler(client, callback_query):
    user_id = callback_query.from_user.id

    try:
        rule_id_str = callback_query.data.split("mod_select_", 1)[1]
        rule_id = ObjectId(rule_id_str)
    except Exception:
        await callback_query.answer("Invalid rule reference.", show_alert=True)
        return

    rule = rules_col.find_one({"_id": rule_id, "user_id": user_id})

    if not rule:
        await callback_query.answer("Configuration profile trace lost.", show_alert=True)
        return

    setup_state[user_id] = {
        "action_mode": "modify",
        "sources": [rule["source_chat"]],
        "target": rule["target_chat"],
        "filters": rule.get("filters", {"text": True, "photo": True, "video": True, "document": True}),
        "chats_cache": [],
        "current_page": 0,
        "created_at": _now(),
    }

    await callback_query.answer()
    await _render_filter_menu(callback_query.message, user_id)


@Client.on_callback_query(filters.regex(r"^menu_remove_rules$"))
async def menu_remove_rules_handler(client, callback_query):
    user_id = callback_query.from_user.id
    rules = get_forward_rules(user_id)
    if not rules:
        await callback_query.answer("No active pipelines to remove.", show_alert=True)
        return

    buttons = []
    for rule in rules:
        buttons.append([
            InlineKeyboardButton(
                f"🗑️ Drop: {rule.get('source_chat')} ➡️ {rule.get('target_chat')}",
                callback_data=f"del_rule_{rule.get('_id')}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")])

    await callback_query.message.edit_text(
        "<b>Select a routing pipeline rule to permanently purge:</b>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^del_rule_"))
async def del_rule_handler(client, callback_query):
    user_id = callback_query.from_user.id

    try:
        rule_id_str = callback_query.data.split("del_rule_", 1)[1]
        rule_object_id = ObjectId(rule_id_str)
        result = rules_col.delete_one({"_id": rule_object_id, "user_id": user_id})

        if result.deleted_count == 0:
            await callback_query.answer("Item trace missing.", show_alert=True)
            return

        await callback_query.message.edit_text(
            "<b>🗑️ Pipeline successfully wiped out of runtime data mappings.</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Back to Settings", callback_data="menu_back")]]),
            parse_mode=ParseMode.HTML,
        )
        await callback_query.answer()
    except Exception as e:
        logger.error("Deletion exception run on profile %s: %s", user_id, e, exc_info=True)
        await callback_query.answer("Error processing purge command.", show_alert=True)


# Passive alert fallback handler for layout labels
@Client.on_callback_query(filters.regex(r"^none_alert$"))
async def none_alert_handler(client, callback_query):
    await callback_query.answer()
