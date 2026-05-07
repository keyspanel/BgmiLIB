"""
storage.py — Unified persistence layer

Priority order (first available wins):
  1. PostgreSQL via psycopg2   — Replit (DATABASE_URL set automatically)
  2. Upstash Redis HTTP API    — Vercel (set UPSTASH_REDIS_REST_URL +
                                         UPSTASH_REDIS_REST_TOKEN in Vercel dashboard)
  3. Local JSON files          — last resort; ephemeral on Vercel so settings
                                 will not survive between invocations.

psycopg2 is imported lazily so a missing module never crashes the import.
requests is used for Upstash HTTP calls (already in requirements.txt).
"""
import os
import json
import requests as _req
from datetime import datetime, timezone, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_VERCEL    = os.environ.get("VERCEL", "") == "1"

_UPSTASH_URL   = os.environ.get("UPSTASH_REDIS_REST_URL",   "").strip().rstrip("/")
_UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()

_PFX        = "bgmi:"
_WIZARD_TTL = 3_600   # 1 hour

# ─────────────────────────────────────────────────────────────────────────────
# Tier detection
# ─────────────────────────────────────────────────────────────────────────────

try:
    import psycopg2 as _psycopg2
    _PG_AVAILABLE = True
except ImportError:
    _psycopg2    = None
    _PG_AVAILABLE = False

_USE_PG      = _PG_AVAILABLE and bool(DATABASE_URL)
_USE_UPSTASH = bool(_UPSTASH_URL) and bool(_UPSTASH_TOKEN)

# Convenience export used by other modules
_USE_DB = _USE_PG or _USE_UPSTASH


# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pg_conn():
    return _psycopg2.connect(DATABASE_URL, connect_timeout=5)


def _ensure_tables() -> None:
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key        TEXT PRIMARY KEY,
                    value      TEXT        NOT NULL,
                    expires_at TIMESTAMPTZ
                )
            """)
        conn.commit()
        print("[DB] Connected to PostgreSQL. Tables ready.", flush=True)
    except Exception as e:
        conn.rollback()
        print(f"[DB] Warning: could not ensure tables: {e}", flush=True)
    finally:
        conn.close()


if _USE_PG:
    try:
        _ensure_tables()
    except Exception as e:
        print(f"[DB] Startup error — disabling PostgreSQL: {e}", flush=True)
        _USE_PG = False
        _USE_DB = _USE_UPSTASH


def _pg_get(key: str) -> str | None:
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value, expires_at FROM kv_store WHERE key = %s", (key,)
            )
            row = cur.fetchone()
            if not row:
                return None
            value, expires_at = row
            if expires_at:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < datetime.now(timezone.utc):
                    with conn.cursor() as cur2:
                        cur2.execute("DELETE FROM kv_store WHERE key = %s", (key,))
                    conn.commit()
                    return None
            return value
    except Exception as e:
        print(f"[DB/PG] get({key}): {e}", flush=True)
        return None
    finally:
        conn.close()


def _pg_set(key: str, value: str, ex: int | None = None) -> None:
    expires_at = None
    if ex:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ex)
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kv_store (key, value, expires_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                  SET value      = EXCLUDED.value,
                      expires_at = EXCLUDED.expires_at
                """,
                (key, value, expires_at),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[DB/PG] set({key}): {e}", flush=True)
    finally:
        conn.close()


def _pg_del(key: str) -> None:
    conn = _pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kv_store WHERE key = %s", (key,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[DB/PG] del({key}): {e}", flush=True)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Upstash Redis HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _upstash(cmd: list) -> object:
    """Send a single Redis command to Upstash via HTTP.  Returns the result value."""
    try:
        r = _req.post(
            _UPSTASH_URL,
            headers={
                "Authorization": f"Bearer {_UPSTASH_TOKEN}",
                "Content-Type":  "application/json",
            },
            data=json.dumps(cmd),
            timeout=5,
        )
        payload = r.json()
        if "error" in payload:
            print(f"[DB/Upstash] error for {cmd[0]}: {payload['error']}", flush=True)
            return None
        return payload.get("result")
    except Exception as e:
        print(f"[DB/Upstash] request failed ({cmd[0]}): {e}", flush=True)
        return None


def _upstash_get(key: str) -> str | None:
    result = _upstash(["GET", key])
    return result if isinstance(result, str) else None


def _upstash_set(key: str, value: str, ex: int | None = None) -> None:
    if ex:
        _upstash(["SETEX", key, ex, value])
    else:
        _upstash(["SET", key, value])


def _upstash_del(key: str) -> None:
    _upstash(["DEL", key])


# ─────────────────────────────────────────────────────────────────────────────
# Startup banner
# ─────────────────────────────────────────────────────────────────────────────

if not _USE_PG and not _USE_UPSTASH:
    if IS_VERCEL:
        print(
            "[DB] WARNING: No persistent storage on Vercel!\n"
            "     Bot settings edited via /botsettings will be lost after each request.\n"
            "     To fix: add UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN\n"
            "     in your Vercel dashboard → Settings → Environment Variables.",
            flush=True,
        )
    elif not _PG_AVAILABLE:
        print("[DB] psycopg2 not available — using local JSON files.", flush=True)
    else:
        print("[DB] DATABASE_URL not set — using local JSON files.", flush=True)
elif _USE_UPSTASH and not _USE_PG:
    print("[DB] Using Upstash Redis for persistent storage.", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Unified low-level key/value API
# ─────────────────────────────────────────────────────────────────────────────

def _db_get(key: str) -> str | None:
    if _USE_PG:
        return _pg_get(key)
    if _USE_UPSTASH:
        return _upstash_get(key)
    return None


def _db_set(key: str, value: str, ex: int | None = None) -> None:
    if _USE_PG:
        _pg_set(key, value, ex)
    elif _USE_UPSTASH:
        _upstash_set(key, value, ex)


def _db_del(key: str) -> None:
    if _USE_PG:
        _pg_del(key)
    elif _USE_UPSTASH:
        _upstash_del(key)


# ─────────────────────────────────────────────────────────────────────────────
# JSON document helpers  (public API)
# ─────────────────────────────────────────────────────────────────────────────

def load_json(redis_key: str, file_path: str, default: dict) -> dict:
    if _USE_PG or _USE_UPSTASH:
        raw = _db_get(_PFX + redis_key)
        if raw:
            try:
                stored = json.loads(raw)
                if isinstance(stored, dict) and isinstance(default, dict):
                    return {**default, **stored}
                return stored
            except Exception:
                pass
        return dict(default)
    # ── local file fallback ──────────────────────────────────────────────────
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
    serialised = json.dumps(data, ensure_ascii=False)
    if _USE_PG or _USE_UPSTASH:
        _db_set(_PFX + redis_key, serialised)
        return
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(serialised)
    except Exception:
        pass  # Vercel /tmp is read-only; silently skip


# ─────────────────────────────────────────────────────────────────────────────
# Wizard / user-data persistence  (Vercel serverless — requires DB)
# ─────────────────────────────────────────────────────────────────────────────

def load_ud(user_id: int) -> dict:
    if not IS_VERCEL or not _USE_DB:
        return {}
    raw = _db_get(f"{_PFX}ud:{user_id}")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def save_ud(user_id: int, data: dict) -> None:
    if not IS_VERCEL or not _USE_DB:
        return
    if data:
        _db_set(f"{_PFX}ud:{user_id}", json.dumps(data), ex=_WIZARD_TTL)
    else:
        _db_del(f"{_PFX}ud:{user_id}")


def del_ud(user_id: int) -> None:
    if IS_VERCEL and _USE_DB:
        _db_del(f"{_PFX}ud:{user_id}")
