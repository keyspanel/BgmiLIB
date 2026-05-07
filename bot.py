#!/usr/bin/env python3

import os
import re
import json
import requests
from html import escape
from urllib.parse import unquote
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

HISTORY_FILE  = "history.json"
MESSAGES_FILE = "messages.json"
MAX_HISTORY   = 10

# ─────────────────────────────────────────────────────────────
# MESSAGE TEMPLATES  (owner-editable via /botsettings)
# Variables:
#   start          → {mention}  {first_name}
#   found          → {username} {uid} {server}
#   not_found      → {uid}
#   invalid_uid    → (none)
#   searching      → (none)
#   conn_failed    → (none)
#   history_header → {count} {max}
#   history_item   → {num} {username} {uid}
#   history_empty  → (none)
# ─────────────────────────────────────────────────────────────

DEFAULT_MESSAGES = {
    "start": (
        "🔥 <b>BGMI ID LOOKUP</b> 🔥\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "👑 Hey {mention} !\n\n"
        "⚔️ Send me a <b>BGMI UID</b> and I'll instantly reveal the player name.\n\n"
        "📌 <b>How to find your UID:</b>\n"
        "Open BGMI 🎮 → Profile icon (top-left) → Number below your name\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 Send UID. Get name. Done.\n"
        "📜 /history — Your last 10 lookups"
    ),
    "found": (
        "💥 <b>Player Found!</b> 🏆\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "👑 <b>Username :</b>  <code>{username}</code>\n"
        "🪪 <b>UID          :</b>  <code>{uid}</code>\n"
        "🌍 <b>Server    :</b>  {server}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>🎯 Tap username or UID to copy.</i>"
    ),
    "not_found": (
        "💀 <b>UID Not Found</b>\n\n"
        "🪪 <code>{uid}</code> has no linked BGMI account.\n\n"
        "🎯 Double-check the UID and try again."
    ),
    "invalid_uid": (
        "⚠️ <b>Invalid UID</b>\n\n"
        "🎯 Send a valid BGMI UID (9–12 digits only)."
    ),
    "searching":    "🔍 <b>Searching player...</b>",
    "conn_failed": (
        "❌ <b>Connection Failed</b>\n\n"
        "🛡️ Could not reach BGMI servers.\n"
        "⚡ Please try again in a moment."
    ),
    "history_header": (
        "📜 <b>Your Last Lookups</b>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    ),
    "history_item": (
        "⭐ <b>{num}.</b>\n"
        "   👑 <code>{username}</code>\n"
        "   🪪 <code>{uid}</code>"
    ),
    "history_empty": (
        "📜 <b>No History Yet</b>\n\n"
        "⚔️ You haven't searched any UIDs.\n"
        "🎯 Send a BGMI UID to get started!"
    ),
}

# Human-readable labels + available vars per key
MSG_META = {
    "start":          ("🚀 Start",           "{mention}  {first_name}"),
    "found":          ("✅ Player Found",     "{username}  {uid}  {server}"),
    "not_found":      ("💀 UID Not Found",    "{uid}"),
    "invalid_uid":    ("⚠️ Invalid UID",      "—"),
    "searching":      ("🔍 Searching",        "—"),
    "conn_failed":    ("❌ Conn. Failed",     "—"),
    "history_header": ("📜 History Header",   "{count}  {max}"),
    "history_item":   ("⭐ History Item",     "{num}  {username}  {uid}"),
    "history_empty":  ("📭 History Empty",    "—"),
}

# ─────────────────────────────────────────────────────────────
# MESSAGES STORE
# ─────────────────────────────────────────────────────────────

def load_messages() -> dict:
    if os.path.exists(MESSAGES_FILE):
        try:
            with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            # fill any missing keys with defaults
            return {**DEFAULT_MESSAGES, **stored}
        except Exception:
            pass
    return dict(DEFAULT_MESSAGES)


def save_messages(data: dict):
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_msg(key: str, **kwargs) -> str:
    """Render a message template with the given variables."""
    tmpl = load_messages().get(key, DEFAULT_MESSAGES.get(key, ""))
    try:
        return tmpl.format(**kwargs)
    except KeyError:
        return tmpl


# ─────────────────────────────────────────────────────────────
# HISTORY STORE
# ─────────────────────────────────────────────────────────────

def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_history(data: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)


def add_to_history(user_id, uid, username):
    h = load_history()
    key = str(user_id)
    h.setdefault(key, [])
    h[key] = [e for e in h[key] if e["uid"] != uid]
    h[key].insert(0, {"uid": uid, "username": username})
    h[key] = h[key][:MAX_HISTORY]
    save_history(h)


def get_history(user_id) -> list:
    return load_history().get(str(user_id), [])


# ─────────────────────────────────────────────────────────────
# BGMI LOOKUP CORE  (github.com/anubhavanonymous/bgmi-id-info)
# ─────────────────────────────────────────────────────────────

def _get_token(session) -> str | None:
    try:
        session.get(
            "https://www.rooter.gg/",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=10,
        )
        raw = session.cookies.get("user_auth")
        if not raw:
            return None
        return json.loads(unquote(raw)).get("accessToken")
    except Exception:
        return None


def get_bgmi_username(user_id: str):
    session = requests.Session()
    token = _get_token(session)
    if not token:
        return None, "token_failed"
    try:
        data = session.get(
            f"https://bazaar.rooter.io/order/getUnipinUsername"
            f"?gameCode=BGMI_IN&id={user_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Device-Type": "web",
                "App-Version": "1.0.0",
                "Device-Id":   "cli-tool",
                "User-Agent":  "Mozilla/5.0",
                "Accept":      "application/json",
            },
            timeout=10,
        ).json()
        if data.get("transaction") == "SUCCESS":
            return data["unipinRes"]["username"], "success"
        return None, data.get("message", "Unknown error")
    except Exception as e:
        return None, str(e)


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def is_valid_uid(text: str) -> bool:
    return bool(re.fullmatch(r"\d{9,12}", text.strip()))


def is_owner(update: Update) -> bool:
    return OWNER_ID and update.effective_user.id == OWNER_ID


def settings_keyboard() -> InlineKeyboardMarkup:
    keys = list(MSG_META.keys())
    rows = []
    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i:i+2]:
            label, _ = MSG_META[k]
            row.append(InlineKeyboardButton(label, callback_data=f"s_edit_{k}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🔒 Close", callback_data="s_close")])
    return InlineKeyboardMarkup(rows)


SAMPLE = {
    "mention":    '<a href="tg://user?id=0">DarkFury</a>',
    "first_name": "DarkFury",
    "username":   "BGMI_PLAYER",
    "uid":        "5123456789",
    "server":     "BGMI — India",
    "count":      "3",
    "max":        str(MAX_HISTORY),
    "num":        "1",
}


# ─────────────────────────────────────────────────────────────
# HANDLERS — USER
# ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    mention = f'<a href="tg://user?id={user.id}">{escape(user.first_name)}</a>'
    await update.message.reply_text(
        get_msg("start", mention=mention, first_name=escape(user.first_name)),
        parse_mode="HTML",
    )


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entries = get_history(update.effective_user.id)
    if not entries:
        await update.message.reply_text(get_msg("history_empty"), parse_mode="HTML")
        return

    msgs = load_messages()
    header = msgs.get("history_header", DEFAULT_MESSAGES["history_header"])
    item_tmpl = msgs.get("history_item", DEFAULT_MESSAGES["history_item"])

    lines = [header, ""]
    for i, e in enumerate(entries, 1):
        try:
            lines.append(item_tmpl.format(
                num=i,
                username=escape(e["username"]),
                uid=escape(e["uid"]),
            ))
        except KeyError:
            lines.append(item_tmpl)

    lines += ["", "━━━━━━━━━━━━━━━━━━━━",
              f"<i>🛡️ {len(entries)}/{MAX_HISTORY} slots used</i>"]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes to edit handler (owner in editing mode) or UID lookup."""
    if is_owner(update) and context.user_data.get("editing_key"):
        await _save_edit(update, context)
        return
    await _lookup_uid(update, context)


async def _lookup_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not is_valid_uid(text):
        await update.message.reply_text(
            get_msg("invalid_uid"),
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
        )
        return

    wait = await update.message.reply_text(
        get_msg("searching"),
        parse_mode="HTML",
        reply_to_message_id=update.message.message_id,
    )
    username, status = get_bgmi_username(text)
    await wait.delete()

    if status == "success":
        add_to_history(update.effective_user.id, text, username)
        await update.message.reply_text(
            get_msg("found",
                    username=escape(username),
                    uid=escape(text),
                    server="BGMI — India"),
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
        )
    elif status == "token_failed":
        await update.message.reply_text(
            get_msg("conn_failed"),
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
        )
    else:
        await update.message.reply_text(
            get_msg("not_found", uid=escape(text)),
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
        )


# ─────────────────────────────────────────────────────────────
# HANDLERS — OWNER /botsettings
# ─────────────────────────────────────────────────────────────

async def cmd_botsettings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("⛔ <b>Owner only.</b>", parse_mode="HTML")
        return
    context.user_data.pop("editing_key", None)
    await update.message.reply_text(
        "🛠 <b>Bot Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Tap any reply below to <b>view and edit</b> it.\n"
        "Changes go live <b>instantly</b> for all users.",
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not (OWNER_ID and q.from_user.id == OWNER_ID):
        await q.answer("⛔ Owner only.", show_alert=True)
        return

    data = q.data

    # ── Close ──
    if data == "s_close":
        await q.message.delete()
        context.user_data.pop("editing_key", None)
        return

    # ── Select message to edit ──
    if data.startswith("s_edit_"):
        key = data[len("s_edit_"):]
        if key not in MSG_META:
            return

        label, avail_vars = MSG_META[key]
        current = load_messages().get(key, DEFAULT_MESSAGES.get(key, ""))

        context.user_data["editing_key"]     = key
        context.user_data["editing_msg_id"]  = q.message.message_id
        context.user_data["editing_chat_id"] = q.message.chat_id

        var_line = (
            f"📌 <b>Available variables:</b>\n<code>{avail_vars}</code>\n\n"
            if avail_vars != "—" else ""
        )

        # Update the menu message to show context (no keyboard)
        context_msg = (
            f"✏️ <b>Editing: {label}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📄 <b>Current message:</b>\n"
            f"<blockquote>{escape(current)}</blockquote>\n\n"
            f"{var_line}"
            "⬇️ <b>Reply to the message below with your new text.</b>\n"
            "Type /cancel to go back."
        )
        await q.message.edit_text(context_msg, parse_mode="HTML")

        # Send a ForceReply prompt the owner must reply to
        force_msg = await q.message.reply_text(
            f"✏️ <b>Send new text for:</b> {label}",
            parse_mode="HTML",
            reply_markup=ForceReply(selective=True, input_field_placeholder=f"New text for {label}..."),
        )
        context.user_data["force_reply_msg_id"] = force_msg.message_id


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    context.user_data.pop("editing_key", None)
    await update.message.reply_text(
        "↩️ <b>Edit cancelled.</b>",
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )


def _capture_html(msg) -> str:
    """Capture owner's message preserving all Telegram formatting (bold, italic, custom emoji, etc.)."""
    try:
        html = getattr(msg, "text_html", None) or getattr(msg, "caption_html", None)
        if html is not None:
            return html.strip()
    except Exception:
        pass
    return (msg.text or msg.caption or "").strip()


async def _save_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key      = context.user_data.pop("editing_key", None)

    if not key:
        return

    new_text = _capture_html(update.message)

    msgs = load_messages()
    msgs[key] = new_text
    save_messages(msgs)

    label, avail_vars = MSG_META.get(key, (key, "—"))

    # Build preview with sample values
    try:
        preview = new_text.format(**SAMPLE)
    except KeyError:
        preview = new_text

    confirm = (
        f"✅ <b>{label}</b> updated!\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📄 <b>Preview:</b>\n"
        f"<blockquote>{preview}</blockquote>\n\n"
        "💾 <i>Changes are live instantly.</i>"
    )
    sent = await update.message.reply_text(
        confirm,
        parse_mode="HTML",
        reply_markup=settings_keyboard(),
    )
    # Pin the settings panel to the new message
    context.user_data["editing_msg_id"]  = sent.message_id
    context.user_data["editing_chat_id"] = sent.chat_id


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN is not set.")
        return
    if not OWNER_ID:
        print("[WARN]  OWNER_ID not set — /botsettings will be disabled.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("history",      cmd_history))
    app.add_handler(CommandHandler("botsettings",  cmd_botsettings))
    app.add_handler(CommandHandler("cancel",       cmd_cancel))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^s_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("[✓] BGMI ID INFO Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
