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
# EMOJI PALETTE
# =====================================
FIRE   = "🔥"
SWORD  = "⚔️"
CROWN  = "👑"
TARGET = "🎯"
TROPHY = "🏆"
BOOM   = "💥"
STAR   = "⭐"
SHIELD = "🛡️"
ALIEN  = "👾"
GAME   = "🎮"
SEARCH = "🔍"
CHECK  = "✅"
CROSS  = "❌"
WARN   = "⚠️"
SCROLL = "📜"
PIN    = "📌"
ID_    = "🪪"
GLOBE  = "🌍"
SKULL  = "💀"
BOLT   = "⚡"

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
    history[key] = [e for e in history[key] if e["uid"] != uid]
    history[key].insert(0, {"uid": uid, "username": username})
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        session.get(url, headers=headers, timeout=10)
        user_auth = session.cookies.get("user_auth")
        if not user_auth:
            return None
        access_token_data = json.loads(unquote(user_auth))
        return access_token_data.get("accessToken")
    except Exception:
        return None


def get_bgmi_username(user_id):
    session = requests.Session()
    access_token = get_authorization_token(session)
    if not access_token:
        return None, "token_failed"

    url = (
        f"https://bazaar.rooter.io/order/getUnipinUsername"
        f"?gameCode=BGMI_IN&id={user_id}"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Device-Type": "web",
        "App-Version": "1.0.0",
        "Device-Id": "cli-tool",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    try:
        data = session.get(url, headers=headers, timeout=10).json()
        if data.get("transaction") == "SUCCESS":
            return data["unipinRes"]["username"], "success"
        return None, data.get("message", "Unknown error")
    except Exception as e:
        return None, str(e)


# =====================================
# UID VALIDATOR
# =====================================

def is_valid_uid(text):
    return bool(re.fullmatch(r"\d{9,12}", text.strip()))


# =====================================
# TELEGRAM HANDLERS
# =====================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    mention = f'<a href="tg://user?id={user.id}">{escape(user.first_name)}</a>'

    msg = (
        f"{FIRE} <b>BGMI ID LOOKUP</b> {FIRE}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{CROWN} Hey {mention} !\n\n"
        f"{SWORD} Send me a <b>BGMI UID</b> — I'll instantly reveal the player name.\n\n"
        f"{PIN} <b>Where to find your UID:</b>\n"
        f"{GAME} Open BGMI → Tap Profile (top-left) → Number below your name\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{TARGET} Send UID. Get name. Done. {CHECK}\n\n"
        f"{SCROLL} /history — View your last {MAX_HISTORY} lookups"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    entries = get_history(user.id)

    if not entries:
        await update.message.reply_text(
            f"{SCROLL} <b>No History Yet</b>\n\n"
            f"{SWORD} You haven't searched any UIDs.\n"
            f"{TARGET} Send a BGMI UID to get started!",
            parse_mode="HTML",
        )
        return

    lines = [f"{SCROLL} <b>Your Last Lookups</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
    for i, entry in enumerate(entries, 1):
        lines.append(
            f"{STAR} <b>{i}.</b>\n"
            f"   {CROWN} <code>{escape(entry['username'])}</code>\n"
            f"   {ID_} <code>{escape(entry['uid'])}</code>"
        )
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>{SHIELD} {len(entries)}/{MAX_HISTORY} slots used</i>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not is_valid_uid(text):
        await update.message.reply_text(
            f"{WARN} <b>Invalid UID</b>\n\n"
            f"{TARGET} Send a valid BGMI UID (9–12 digits only).",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
        )
        return

    waiting = await update.message.reply_text(
        f"{SEARCH} <b>Searching player...</b>",
        parse_mode="HTML",
        reply_to_message_id=update.message.message_id,
    )

    username, status = get_bgmi_username(text)
    await waiting.delete()

    if status == "success":
        add_to_history(update.effective_user.id, text, username)
        await update.message.reply_text(
            f"{BOOM} <b>Player Found!</b> {TROPHY}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{CROWN} <b>Username :</b>  <code>{escape(username)}</code>\n"
            f"{ID_} <b>UID          :</b>  <code>{escape(text)}</code>\n"
            f"{GLOBE} <b>Server    :</b>  BGMI — India\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>{TARGET} Tap username or UID to copy.</i>",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
        )
    elif status == "token_failed":
        await update.message.reply_text(
            f"{CROSS} <b>Connection Failed</b>\n\n"
            f"{SHIELD} Could not reach BGMI servers.\n"
            f"{BOLT} Please try again in a moment.",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
        )
    else:
        await update.message.reply_text(
            f"{SKULL} <b>UID Not Found</b>\n\n"
            f"{ID_} <code>{escape(text)}</code> has no linked BGMI account.\n\n"
            f"{TARGET} Double-check the UID and try again.",
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
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
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_uid))

    print("[✓] BGMI ID INFO Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
