"""
storage.py  —  Unified persistence layer

  Production (Vercel)  →  Upstash Redis via REST API  (no extra packages)
  Local / Replit       →  JSON files  (existing behaviour, unchanged)

Set UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN env vars to activate Redis.
"""
import os
import json
import requests

REDIS_URL   = os.environ.get("UPSTASH_REDIS_REST_URL",  "").rstrip("/")
REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
IS_VERCEL   = bool(REDIS_URL)

_PFX = "bgmi:"


# ─────────────────────────────────────────────────────────────────────────────
# Upstash Redis REST helpers
# ─────────────────────────────────────────────────────────────────────────────

def _cmd(*args):
    """Execute any Redis command via Upstash REST API.
    POST body is a JSON array: ["COMMAND", "arg1", "arg2", ...]
    Returns the 'result' field on success, None on any error."""
    if not IS_VERCEL:
        return None
    try:
        r = requests.post(
            REDIS_URL,
            headers={
                "Authorization": f"Bearer {REDIS_TOKEN}",
                "Content-Type":  "application/json",
            },
            json=list(args),
            timeout=5,
        )
        return r.json().get("result")
    except Exception:
        return None


def r_get(key: str) -> str | None:
    """GET a string value from Redis."""
    return _cmd("GET", _PFX + key)


def r_set(key: str, value: str, ex: int | None = None) -> None:
    """SET a string value in Redis, with optional TTL in seconds."""
    if ex:
        _cmd("SET", _PFX + key, value, "EX", ex)
    else:
        _cmd("SET", _PFX + key, value)


def r_del(key: str) -> None:
    """DEL a key from Redis."""
    _cmd("DEL", _PFX + key)


# ─────────────────────────────────────────────────────────────────────────────
# JSON document helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_json(redis_key: str, file_path: str, default: dict) -> dict:
    """Load a JSON document.

    Always shallow-merges `default` so newly added default keys always appear
    even when an older stored document doesn't have them yet.
    """
    if IS_VERCEL:
        raw = r_get(redis_key)
        if raw:
            try:
                stored = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(stored, dict) and isinstance(default, dict):
                    return {**default, **stored}
                return stored
            except Exception:
                pass
        return dict(default)
    else:
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    stored = json.load(f)
                if isinstance(stored, dict) and isinstance(default, dict):
                    return {**default, **stored}
                return stored
            except Exception:
                pass
        return dict(default)


def save_json(redis_key: str, file_path: str, data) -> None:
    """Persist a JSON document to Redis (production) or a local file."""
    serialised = json.dumps(data, ensure_ascii=False)
    if IS_VERCEL:
        r_set(redis_key, serialised)
    else:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(serialised)


# ─────────────────────────────────────────────────────────────────────────────
# Wizard / user-data persistence
# ─────────────────────────────────────────────────────────────────────────────
# On Vercel each webhook request is a fresh function invocation, so
# context.user_data is empty.  We persist wizard state in Redis and
# restore it at the start of every wizard-related handler via the
# @with_persistent_ud decorator in bot.py.

_WIZARD_TTL = 3_600   # 1 hour — stale wizard sessions expire automatically


def load_ud(user_id: int) -> dict:
    """Load persisted wizard state for a user from Redis.
    Returns {} when not on Vercel (context.user_data is sufficient locally)."""
    if not IS_VERCEL:
        return {}
    raw = r_get(f"ud:{user_id}")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def save_ud(user_id: int, data: dict) -> None:
    """Persist wizard state for a user to Redis with a TTL."""
    if not IS_VERCEL:
        return
    if data:
        r_set(f"ud:{user_id}", json.dumps(data), ex=_WIZARD_TTL)
    else:
        r_del(f"ud:{user_id}")


def del_ud(user_id: int) -> None:
    """Delete wizard state for a user from Redis."""
    if IS_VERCEL:
        r_del(f"ud:{user_id}")
