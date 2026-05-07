"""
storage.py — Unified persistence layer

  Replit  (polling)    →  PostgreSQL via psycopg2 if DATABASE_URL is set,
                          otherwise local JSON files.
  Vercel  (serverless) →  Local JSON files (psycopg2 not available on Vercel;
                          each invocation is stateless which is fine for webhooks).

psycopg2 is imported lazily — a missing module never crashes the import.
"""
import os
import json
from datetime import datetime, timezone, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_VERCEL    = os.environ.get("VERCEL", "") == "1"

_PFX        = "bgmi:"
_WIZARD_TTL = 3_600   # 1 hour

# Try to import psycopg2 once at startup; disable DB if unavailable.
try:
    import psycopg2 as _psycopg2
    _PG_AVAILABLE = True
except ImportError:
    _psycopg2    = None
    _PG_AVAILABLE = False

# Only use the DB if the driver is present AND a URL is configured.
_USE_DB = _PG_AVAILABLE and bool(DATABASE_URL)


# ─────────────────────────────────────────────────────────────────────────────
# Connection helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_conn():
    return _psycopg2.connect(DATABASE_URL, connect_timeout=5)


def _ensure_tables() -> None:
    conn = _get_conn()
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


if _USE_DB:
    try:
        _ensure_tables()
    except Exception as e:
        print(f"[DB] Startup error: {e}", flush=True)
        _USE_DB = False
elif not _PG_AVAILABLE:
    print("[DB] psycopg2 not available — using local JSON files.", flush=True)
else:
    print("[DB] DATABASE_URL not set — using local JSON files.", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Low-level key/value helpers
# ─────────────────────────────────────────────────────────────────────────────

def _db_get(key: str) -> str | None:
    conn = _get_conn()
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
        print(f"[DB] _db_get({key}): {e}", flush=True)
        return None
    finally:
        conn.close()


def _db_set(key: str, value: str, ex: int | None = None) -> None:
    expires_at = None
    if ex:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ex)
    conn = _get_conn()
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
        print(f"[DB] _db_set({key}): {e}", flush=True)
    finally:
        conn.close()


def _db_del(key: str) -> None:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM kv_store WHERE key = %s", (key,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[DB] _db_del({key}): {e}", flush=True)
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# JSON document helpers  (public API — unchanged signatures)
# ─────────────────────────────────────────────────────────────────────────────

def load_json(redis_key: str, file_path: str, default: dict) -> dict:
    if _USE_DB:
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
    if _USE_DB:
        _db_set(_PFX + redis_key, serialised)
    else:
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
