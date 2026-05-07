#!/usr/bin/env python3

import os
import re
import json
import requests
from urllib.parse import unquote
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

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
    text = text.strip()
    return bool(re.fullmatch(r'\d{9,12}', text))


# =====================================
# TELEGRAM HANDLERS
# =====================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👾 *BGMI ID INFO BOT*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send me any *BGMI UID* and I'll instantly fetch the *in-game username* linked to it\\.\n\n"
        "📌 *How to find your UID:*\n"
        "Open BGMI → Tap your profile avatar \\(top\\-left\\) → Your UID is below your username\\.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Just send the UID\\.  That's it\\. ✅"
    )
    await update.message.reply_text(msg, parse_mode="MarkdownV2")


async def handle_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not is_valid_uid(text):
        await update.message.reply_text(
            "⚠️ *Invalid UID*\n\nPlease send a valid BGMI UID \\(9\\-12 digit number\\)\\.",
            parse_mode="MarkdownV2",
            reply_to_message_id=update.message.message_id
        )
        return

    searching_msg = await update.message.reply_text(
        "🔍 *Searching\\.\\.\\.*",
        parse_mode="MarkdownV2",
        reply_to_message_id=update.message.message_id
    )

    username, status = get_bgmi_username(text)

    await searching_msg.delete()

    if status == "success":
        reply = (
            "✅ *BGMI Player Found*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *Username :* `{username}`\n"
            f"🆔 *UID       :* `{text}`\n"
            f"🌍 *Server   :* `BGMI — India`\n\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        await update.message.reply_text(
            reply,
            parse_mode="Markdown",
            reply_to_message_id=update.message.message_id
        )
    elif status == "token_failed":
        await update.message.reply_text(
            "❌ *Failed to connect to BGMI servers\\.*\n\nPlease try again in a moment\\.",
            parse_mode="MarkdownV2",
            reply_to_message_id=update.message.message_id
        )
    else:
        safe_status = status.replace("_", "\\_").replace(".", "\\.").replace("!", "\\!").replace("-", "\\-").replace("(", "\\(").replace(")", "\\)")
        await update.message.reply_text(
            f"❌ *UID Not Found*\n\n`{text}` does not match any BGMI account\\.\n\nDouble\\-check the UID and try again\\.",
            parse_mode="MarkdownV2",
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_uid))

    print("[✓] BGMI ID INFO Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
