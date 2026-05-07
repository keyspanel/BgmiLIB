#!/usr/bin/env python3

import os
import re
import json
import requests
from html import escape
from urllib.parse import unquote
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
HISTORY_FILE = "history.json"
MAX_HISTORY = 10

# =====================================
# HISTORY STORAGE
# =====================================

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_history(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)


def add_to_history(user_id, uid, username):
    history = load_history()
    key = str(user_id)
    if key not in history:
        history[key] = []
    # Remove duplicate if same UID exists
    history[key] = [e for e in history[key] if e["uid"] != uid]
    # Add new entry at front
    history[key].insert(0, {"uid": uid, "username": username})
    # Keep only last MAX_HISTORY
    history[key] = history[key][:MAX_HISTORY]
    save_history(history)


def get_history(user_id):
    history = load_history()
    return history.get(str(user_id), [])


# =====================================
# BGMI LOOKUP CORE (from bgmi-id-info)
# =====================================

def get_authorization_token(session):
    url = "https://www.rooter.gg/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        session.get(url, headers=headers, timeout=10)
        user_auth = session.cookies.get("user_auth")
        if not user_auth:
            return None
        access_token_json = unquote(user_auth)
        access_token_data = json.loads(access_token_json)
        return access_token_data.get("accessToken")
    except Exception:
        return None


def get_bgmi_username(user_id):
    session = requests.Session()
    access_token = get_authorization_token(session)
    if not access_token:
        return None, "token_failed"

    url = f"https://bazaar.rooter.io/order/getUnipinUsername?gameCode=BGMI_IN&id={user_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Device-Type": "web",
        "App-Version": "1.0.0",
        "Device-Id": "cli-tool",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }

    try:
        response = session.get(url, headers=headers, timeout=10)
        data = response.json()
        if data.get("transaction") == "SUCCESS":
            username = data["unipinRes"]["username"]
            return username, "success"
        else:
            msg = data.get("message", "Unknown error")
            return None, msg
    except Exception as e:
        return None, str(e)


# =====================================
# UID VALIDATOR
# =====================================

def is_valid_uid(text):
    return bool(re.fullmatch(r'\d{9,12}', text.strip()))


# =====================================
# TELEGRAM HANDLERS
# =====================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    mention = f'<a href="tg://user?id={user.id}">{escape(user.first_name)}</a>'

    msg = (
        f"Hey {mention} !\n"
        "Send me any <b>BGMI UID</b> and I'll instantly fetch the in-game username linked to it.\n\n"
        "📌 <b>How to find your UID:</b>\n"
        "Open BGMI → Tap your profile avatar (top-left) → Your UID is below your username.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Just send the UID. That's it. ✅\n\n"
        "📜 Use /history to see your last 10 lookups."
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    entries = get_history(user.id)

    if not entries:
        await update.message.reply_text(
            "📭 <b>No History Found</b>\n\nYou haven't looked up any UIDs yet.\nSend a BGMI UID to get started!",
            parse_mode="HTML"
        )
        return

    lines = ["📜 <b>Your Last Lookups</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
    for i, entry in enumerate(entries, 1):
        lines.append(
            f"<b>{i}.</b> 👤 <code>{escape(entry['username'])}</code>\n"
            f"     🆔 <code>{escape(entry['uid'])}</code>"
        )

    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>Showing {len(entries)} of last {MAX_HISTORY} lookups.</i>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not is_valid_uid(text):
        await update.message.reply_text(
            "⚠️ <b>Invalid UID</b>\n\nPlease send a valid BGMI UID (9–12 digit number).",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )
        return

    searching_msg = await update.message.reply_text(
        "🔍 <b>Searching...</b>",
        parse_mode="HTML",
        reply_to_message_id=update.message.message_id
    )

    username, status = get_bgmi_username(text)
    await searching_msg.delete()

    if status == "success":
        add_to_history(update.effective_user.id, text, username)
        reply = (
            "✅ <b>BGMI Player Found</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Username :</b> <code>{escape(username)}</code>\n"
            f"🆔 <b>UID       :</b> <code>{escape(text)}</code>\n"
            f"🌍 <b>Server   :</b> BGMI — India\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Tap username or UID to copy.</i>"
        )
        await update.message.reply_text(
            reply,
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )
    elif status == "token_failed":
        await update.message.reply_text(
            "❌ <b>Failed to connect to BGMI servers.</b>\n\nPlease try again in a moment.",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )
    else:
        await update.message.reply_text(
            f"❌ <b>UID Not Found</b>\n\n<code>{escape(text)}</code> does not match any BGMI account.\n\nDouble-check the UID and try again.",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id
        )


# =====================================
# MAIN
# =====================================

def main():
    if not TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN is not set.")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_uid))

    print("[✓] BGMI ID INFO Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
