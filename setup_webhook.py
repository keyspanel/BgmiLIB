#!/usr/bin/env python3
"""
setup_webhook.py — Register the Telegram webhook (run once after deploying to Vercel).

Usage
-----
Set these environment variables, then run:

    python setup_webhook.py

Required:
    TELEGRAM_BOT_TOKEN   — your bot token from @BotFather
    WEBHOOK_URL          — full URL of your Vercel function, e.g.
                           https://your-project.vercel.app/api/webhook

Optional:
    WEBHOOK_SECRET       — a random string; if set, every request is verified
                           against the X-Telegram-Bot-Api-Secret-Token header

To DELETE / unset the webhook (switch back to polling):
    DELETE_WEBHOOK=1 python setup_webhook.py
"""
import os
import asyncio
from telegram import Bot


async def main() -> None:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    webhook = os.environ.get("WEBHOOK_URL", "").strip()
    secret  = os.environ.get("WEBHOOK_SECRET", "").strip()
    delete  = os.environ.get("DELETE_WEBHOOK", "").strip()

    if not token:
        print("❌  TELEGRAM_BOT_TOKEN is not set.")
        return

    bot = Bot(token=token)

    if delete:
        await bot.delete_webhook(drop_pending_updates=True)
        print("✅  Webhook deleted.  Bot will fall back to polling.")
        return

    if not webhook:
        print("❌  WEBHOOK_URL is not set.")
        print("    Example: https://your-project.vercel.app/api/webhook")
        return

    await bot.set_webhook(
        url=webhook,
        secret_token=secret or None,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

    info = await bot.get_webhook_info()
    print("✅  Webhook registered!")
    print(f"    URL             : {info.url}")
    print(f"    Pending updates : {info.pending_update_count}")
    print(f"    Secret set      : {'Yes' if secret else 'No (not recommended)'}")
    if info.last_error_message:
        print(f"    Last error      : {info.last_error_message}")


asyncio.run(main())
