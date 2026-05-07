"""
api/webhook.py  —  Vercel serverless entry point for the BGMI Lookup Bot.

Architecture
────────────
  • One asyncio event loop  (_loop) per lambda instance.
    Reused across warm invocations so the Bot's httpx client is never
    recreated needlessly.
  • One Application (_app) per lambda instance, initialized once on cold start.
  • Each POST request carries exactly one Telegram Update.
    We feed it to _app.process_update() and return 200 OK immediately.
  • Optional WEBHOOK_SECRET verification via the standard Telegram header.

Cold-start sequence (happens once per new lambda instance):
  1.  sys.path is patched so we can import from the project root.
  2.  bot.build_app() builds the Application (no updater, webhook mode).
  3.  _app.initialize() — creates the Bot's httpx client on _loop.
  4.  _app.start()     — starts PTB's internal update processor.
  All subsequent warm invocations skip straight to process_update().
"""

import json
import sys
import os
import asyncio
import traceback
from http.server import BaseHTTPRequestHandler

# ── Add project root to sys.path so we can import bot / storage ──────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from telegram import Update          # noqa: E402  (after sys.path patch)
import bot as _bot                   # noqa: E402

# ── One event loop + one Application per lambda instance (cold start) ────────
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)

_app = _bot.build_app()
_loop.run_until_complete(_app.initialize())
_loop.run_until_complete(_app.start())

_WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")


# ─────────────────────────────────────────────────────────────────────────────
# Vercel requires the handler class to be named exactly `handler`
# ─────────────────────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):  # noqa: N801

    # ── POST — receive Telegram update ───────────────────────────────────────
    def do_POST(self):
        # 1. Verify webhook secret (strongly recommended in production)
        if _WEBHOOK_SECRET:
            given = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if given != _WEBHOOK_SECRET:
                self._send(403, b"Forbidden")
                return

        # 2. Read the JSON body
        try:
            n    = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            data = json.loads(body)
        except Exception:
            self._send(400, b"Bad Request")
            return

        # 3. Deserialise and process the update
        try:
            update = Update.de_json(data, _app.bot)
            _loop.run_until_complete(_app.process_update(update))
        except Exception:
            # Log but always return 200 so Telegram doesn't retry
            print(f"[Webhook Error]\n{traceback.format_exc()}", flush=True)

        self._send(200, b"OK")

    # ── GET — health check ────────────────────────────────────────────────────
    def do_GET(self):
        self._send(200, b'{"ok":true,"service":"BGMI Lookup Bot"}')

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # suppress Vercel's default per-request stderr noise
