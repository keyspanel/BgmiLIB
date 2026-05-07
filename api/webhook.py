"""
api/webhook.py  —  Vercel serverless entry point for the BGMI Lookup Bot.
"""

import json
import sys
import os
import asyncio
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

# ── Add project root to sys.path so we can import bot / storage ──────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from telegram import Update
import bot as _bot

_WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# ── Cold start: initialise once per lambda instance ──────────────────────────
# Wrapped in try/except so a missing env var returns a 500 with a clear message
# instead of silently crashing the function invocation.
_loop: asyncio.AbstractEventLoop | None = None
_app = None
_init_error: str | None = None

try:
    print("[Webhook] Cold start — initialising bot...", flush=True)
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _app = _bot.build_app()
    _loop.run_until_complete(_app.initialize())
    _loop.run_until_complete(_app.start())
    print("[Webhook] Bot ready to handle updates.", flush=True)
except Exception as _e:
    _init_error = str(_e)
    print(f"[Webhook] INIT ERROR: {_init_error}\n{traceback.format_exc()}", flush=True)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


class handler(BaseHTTPRequestHandler):

    # ── POST — receive Telegram update ───────────────────────────────────────
    def do_POST(self):
        if _init_error:
            body = f"Bot init failed: {_init_error}".encode()
            self._send(500, body)
            return

        # 1. Verify webhook secret
        if _WEBHOOK_SECRET:
            given = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if given != _WEBHOOK_SECRET:
                print(f"[{_ts()}] Webhook: 403 invalid secret token", flush=True)
                self._send(403, b"Forbidden")
                return

        # 2. Read the JSON body
        try:
            n    = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            data = json.loads(body)
        except Exception as e:
            print(f"[{_ts()}] Webhook: 400 bad request — {e}", flush=True)
            self._send(400, b"Bad Request")
            return

        update_id   = data.get("update_id", "?")
        update_type = (
            "message"        if "message"        in data else
            "callback_query" if "callback_query" in data else
            "unknown"
        )
        print(f"[{_ts()}] Webhook: update_id={update_id} type={update_type}", flush=True)

        # 3. Process the update
        try:
            update = Update.de_json(data, _app.bot)
            _loop.run_until_complete(_app.process_update(update))
            print(f"[{_ts()}] Webhook: update_id={update_id} processed OK", flush=True)
        except Exception:
            print(
                f"[{_ts()}] Webhook: ERROR processing update_id={update_id}\n"
                f"{traceback.format_exc()}",
                flush=True,
            )

        # Always return 200 so Telegram does not retry
        self._send(200, b"OK")

    # ── GET — health check ────────────────────────────────────────────────────
    def do_GET(self):
        if _init_error:
            body = json.dumps({"ok": False, "error": _init_error}).encode()
            self._send(500, body)
            return
        print(f"[{_ts()}] Webhook: GET health check", flush=True)
        self._send(200, b'{"ok":true,"service":"BGMI Lookup Bot"}')

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
