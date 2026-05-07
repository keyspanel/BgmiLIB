#!/usr/bin/env python3

import os
import re
import json
import requests
from html import escape
from urllib.parse import unquote
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply, LinkPreviewOptions
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

HISTORY_FILE   = "history.json"
MESSAGES_FILE  = "messages.json"
STATS_FILE     = "stats.json"
MAX_HISTORY    = 10
BOT_START_TIME = datetime.now(timezone.utc)

# ─────────────────────────────────────────────────────────────
# BOTSETTINGS — wizard step constants
# ─────────────────────────────────────────────────────────────
BS_IDLE      = "idle"
BS_EDIT_TEXT = "edit_text"
BS_BTN_TEXT  = "btn_text"
BS_BTN_URL   = "btn_url"
BS_BTN_EMOJI = "btn_emoji"

STYLE_OPTIONS = ["default", "primary", "success", "danger"]
STYLE_LABEL   = {
    "default": "⬜ Default",
    "primary": "🔵 Primary",
    "success": "🟢 Success",
    "danger":  "🔴 Danger",
}

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
    "start_preview_url": "",
}

# Human-readable labels + available vars per key
MSG_META = {
    "start":             ("🚀 Start",           ["{mention}", "{first_name}"]),
    "found":             ("✅ Player Found",     ["{username}", "{uid}", "{server}"]),
    "not_found":         ("💀 UID Not Found",    ["{uid}"]),
    "invalid_uid":       ("⚠️ Invalid UID",      []),
    "searching":         ("🔍 Searching",        []),
    "conn_failed":       ("❌ Conn. Failed",     []),
    "history_header":    ("📜 History Header",   ["{count}", "{max}"]),
    "history_item":      ("⭐ History Item",     ["{num}", "{username}", "{uid}"]),
    "history_empty":     ("📭 History Empty",    []),
    "start_preview_url": ("🖼️ Start Image URL",  []),
}

# Keys that hold plain values (not HTML-formatted Telegram messages)
# These get plain-text editing prompts and no button management
PLAIN_KEYS = {"start_preview_url"}

# Sample values for preview rendering
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
# MESSAGES STORE
# ─────────────────────────────────────────────────────────────

def load_messages() -> dict:
    if os.path.exists(MESSAGES_FILE):
        try:
            with open(MESSAGES_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            return {**DEFAULT_MESSAGES, **stored}
        except Exception:
            pass
    return dict(DEFAULT_MESSAGES)


def save_messages(data: dict):
    with open(MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_msg(key: str, **kwargs) -> str:
    tmpl = load_messages().get(key, DEFAULT_MESSAGES.get(key, ""))
    try:
        return tmpl.format(**kwargs)
    except KeyError:
        return tmpl


# ─────────────────────────────────────────────────────────────
# INLINE BUTTON STORE  (per-message-key)
# Stored in messages.json as "{key}_buttons" → JSON array
# Each item: {"text": "...", "url": "...", "style": "primary",
#             "icon_custom_emoji_id": "..."}
# ─────────────────────────────────────────────────────────────

def _btn_key(key: str) -> str:
    return f"{key}_buttons"


def load_buttons(key: str) -> list[dict]:
    raw = load_messages().get(_btn_key(key), "")
    if not raw:
        return []
    try:
        items = json.loads(raw)
        return items if isinstance(items, list) else []
    except Exception:
        return []


def save_buttons(key: str, buttons: list[dict]):
    msgs = load_messages()
    msgs[_btn_key(key)] = json.dumps(buttons, ensure_ascii=False)
    save_messages(msgs)


def build_inline_keyboard(key: str) -> InlineKeyboardMarkup | None:
    """Build InlineKeyboardMarkup from saved buttons for a message key."""
    buttons = load_buttons(key)
    if not buttons:
        return None
    rows = []
    for btn in buttons:
        text = btn.get("text", "").strip()
        url  = btn.get("url", "").strip()
        if not text or not url:
            continue
        api_kwargs = {}
        style = btn.get("style", "").strip()
        if style in ("primary", "success", "danger"):
            api_kwargs["style"] = style
        emoji_id = btn.get("icon_custom_emoji_id", "").strip()
        if emoji_id:
            api_kwargs["icon_custom_emoji_id"] = emoji_id
        rows.append([InlineKeyboardButton(
            text=text, url=url,
            api_kwargs=api_kwargs if api_kwargs else None,
        )])
    return InlineKeyboardMarkup(rows) if rows else None


def _buttons_summary(buttons: list[dict]) -> str:
    if not buttons:
        return "  <i>No buttons configured.</i>"
    lines = []
    for i, b in enumerate(buttons, 1):
        style_tag = f" [{b.get('style') or 'default'}]"
        emoji_tag = f" 🎨" if b.get("icon_custom_emoji_id") else ""
        lines.append(
            f"  <b>{i}.</b> {escape(b.get('text',''))} "
            f"→ <code>{escape(b.get('url',''))}</code>"
            f"{style_tag}{emoji_tag}"
        )
    return "\n".join(lines)


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
# STATS STORE
# stats.json schema:
#   total_lookups, total_found, total_not_found, total_conn_failed
#   daily:        { "YYYY-MM-DD": { lookups, found, not_found, conn_failed } }
#   uid_counts:   { "uid": { count, last_username } }
#   user_activity:{ "user_id": { count, first_name, first_seen, last_seen } }
# ─────────────────────────────────────────────────────────────

def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _empty_stats() -> dict:
    return {
        "total_lookups":    0,
        "total_found":      0,
        "total_not_found":  0,
        "total_conn_failed": 0,
        "daily":            {},
        "uid_counts":       {},
        "user_activity":    {},
    }


def load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                stored = json.load(f)
            base = _empty_stats()
            base.update(stored)
            return base
        except Exception:
            pass
    return _empty_stats()


def save_stats(data: dict):
    with open(STATS_FILE, "w") as f:
        json.dump(data, f)


def record_lookup(user_id: int, uid: str, username: str | None,
                  first_name: str, status: str):
    """Record one lookup event. status: 'found' | 'not_found' | 'conn_failed'"""
    s     = load_stats()
    today = _today_str()

    s["total_lookups"]        = s.get("total_lookups", 0) + 1
    stat_key                  = f"total_{status}"
    s[stat_key]               = s.get(stat_key, 0) + 1

    # Daily bucket
    day = s.setdefault("daily", {}).setdefault(today, {
        "lookups": 0, "found": 0, "not_found": 0, "conn_failed": 0
    })
    day["lookups"] = day.get("lookups", 0) + 1
    day[status]    = day.get(status, 0) + 1

    # UID frequency (found lookups only)
    if status == "found" and username:
        uc    = s.setdefault("uid_counts", {})
        entry = uc.setdefault(uid, {"count": 0, "last_username": ""})
        entry["count"]        += 1
        entry["last_username"] = username

    # Per-user activity
    ua        = s.setdefault("user_activity", {})
    ukey      = str(user_id)
    ua_entry  = ua.setdefault(ukey, {
        "count": 0, "first_name": first_name,
        "first_seen": today, "last_seen": today,
    })
    ua_entry["count"]      += 1
    ua_entry["last_seen"]   = today
    ua_entry["first_name"]  = first_name   # keep name current

    save_stats(s)


# ─────────────────────────────────────────────────────────────
# BGMI LOOKUP CORE
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
    return bool(OWNER_ID and update.effective_user.id == OWNER_ID)


def _capture_html(msg) -> str:
    """Capture message preserving all Telegram formatting (bold, italic, custom emoji, etc.)."""
    try:
        html = getattr(msg, "text_html", None) or getattr(msg, "caption_html", None)
        if html is not None:
            return html.strip()
    except Exception:
        pass
    return (msg.text or msg.caption or "").strip()


def _bs_clear(ud: dict):
    """Clear all botsettings wizard state from user_data."""
    for k in ("bs_step", "bs_key", "bs_current_btn", "bs_prompt_msg_id"):
        ud.pop(k, None)


# ─────────────────────────────────────────────────────────────
# BOTSETTINGS — UI builders
# ─────────────────────────────────────────────────────────────

def _main_menu_keyboard() -> InlineKeyboardMarkup:
    keys = list(MSG_META.keys())
    rows = []
    for i in range(0, len(keys), 2):
        row = []
        for k in keys[i:i+2]:
            label, _ = MSG_META[k]
            if k in PLAIN_KEYS:
                url_val = load_messages().get(k, "").strip()
                suffix  = " ✅" if url_val else " ➕"
            else:
                btn_count = len(load_buttons(k))
                suffix = f" 🔘{btn_count}" if btn_count else ""
            row.append(InlineKeyboardButton(f"{label}{suffix}", callback_data=f"s_v_{k}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Close", callback_data="s_close")])
    return InlineKeyboardMarkup(rows)


def _view_panel_text(key: str) -> str:
    label, vars_list = MSG_META[key]
    current = load_messages().get(key, DEFAULT_MESSAGES.get(key, ""))

    if key in PLAIN_KEYS:
        url_val = current.strip()
        status  = f"<code>{escape(url_val)}</code>" if url_val else "<i>Not set — no image shown on /start</i>"
        return (
            f"⚙️ <b>{label}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🖼️ <b>How it works:</b>\n"
            "When a URL is set, Telegram renders a large image\n"
            "<b>above</b> the /start caption using link preview.\n"
            "This is the same technique used by SayGGBot.\n\n"
            f"📌 <b>Current URL:</b>\n{status}\n\n"
            "<b>Accepted URLs:</b>\n"
            "  • Direct image link: <code>https://example.com/img.jpg</code>\n"
            "  • Telegraph page with image\n"
            "  • Any URL Telegram can generate a preview from\n"
        )

    buttons = load_buttons(key)

    var_line = ""
    if vars_list:
        var_line = "\n📌 <b>Variables:</b>  " + "  ".join(f"<code>{v}</code>" for v in vars_list) + "\n"

    btn_section = (
        f"\n🔘 <b>Buttons ({len(buttons)}):</b>\n"
        f"{_buttons_summary(buttons)}\n"
    ) if buttons else "\n🔘 <b>Buttons:</b>  <i>None</i>\n"

    return (
        f"⚙️ <b>{label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📄 <b>Current Message:</b>\n"
        f"<blockquote>{escape(current)}</blockquote>\n"
        f"{var_line}"
        f"{btn_section}"
    )


def _view_panel_keyboard(key: str) -> InlineKeyboardMarkup:
    if key in PLAIN_KEYS:
        current = load_messages().get(key, "").strip()
        rows = [
            [InlineKeyboardButton("✏️ Set URL", callback_data=f"s_et_{key}")],
        ]
        if current:
            rows.append([InlineKeyboardButton("🗑️ Clear URL", callback_data=f"s_rt_{key}")])
        rows.append([
            InlineKeyboardButton("◀️ Back",  callback_data="s_back"),
            InlineKeyboardButton("❌ Close", callback_data="s_close"),
        ])
        return InlineKeyboardMarkup(rows)

    buttons = load_buttons(key)
    rows = [
        [
            InlineKeyboardButton("✏️ Edit Text",      callback_data=f"s_et_{key}"),
            InlineKeyboardButton("🔘 Manage Buttons", callback_data=f"s_mb_{key}"),
        ],
        [
            InlineKeyboardButton("👁️ Preview",        callback_data=f"s_pv_{key}"),
            InlineKeyboardButton("🔄 Reset Text",     callback_data=f"s_rt_{key}"),
        ],
    ]
    if buttons:
        rows.append([InlineKeyboardButton("🗑️ Clear All Buttons", callback_data=f"s_rb_{key}")])
    rows.append([
        InlineKeyboardButton("◀️ Back",  callback_data="s_back"),
        InlineKeyboardButton("❌ Close", callback_data="s_close"),
    ])
    return InlineKeyboardMarkup(rows)


def _manage_buttons_keyboard(key: str) -> InlineKeyboardMarkup:
    buttons = load_buttons(key)
    rows = []
    for i, b in enumerate(buttons):
        style_tag = f" [{b.get('style') or 'default'}]"
        label_txt = escape(b.get("text", f"Button {i+1}"))
        rows.append([
            InlineKeyboardButton(f"🔘 {label_txt}{style_tag}", callback_data=f"s_noop"),
            InlineKeyboardButton(f"🗑️ #{i+1}",                callback_data=f"s_db_{key}_{i}"),
        ])
    rows.append([InlineKeyboardButton("➕ Add Button", callback_data=f"s_ab_{key}")])
    rows.append([
        InlineKeyboardButton("◀️ Back to Settings", callback_data=f"s_v_{key}"),
        InlineKeyboardButton("❌ Close",             callback_data="s_close"),
    ])
    return InlineKeyboardMarkup(rows)


def _manage_buttons_text(key: str) -> str:
    label, _ = MSG_META[key]
    buttons   = load_buttons(key)
    count     = len(buttons)
    return (
        f"🔘 <b>Button Manager — {label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Configured buttons ({count}/10):</b>\n"
        f"{_buttons_summary(buttons)}\n\n"
        "<b>How to add buttons:</b>\n"
        "Tap <b>➕ Add Button</b> and follow the steps:\n"
        "  1️⃣  Button label text\n"
        "  2️⃣  Destination URL\n"
        "  3️⃣  Button style (colour)\n"
        "  4️⃣  Custom emoji icon (optional)\n\n"
        "<i>Buttons appear below the bot reply when users trigger it.</i>"
    )


def _style_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬜ Default", callback_data="s_bs_default"),
            InlineKeyboardButton("🔵 Primary", callback_data="s_bs_primary"),
        ],
        [
            InlineKeyboardButton("🟢 Success", callback_data="s_bs_success"),
            InlineKeyboardButton("🔴 Danger",  callback_data="s_bs_danger"),
        ],
    ])


# ─────────────────────────────────────────────────────────────
# HANDLERS — USER
# ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user        = update.effective_user
    mention     = f'<a href="tg://user?id={user.id}">{escape(user.first_name)}</a>'
    text        = get_msg("start", mention=mention, first_name=escape(user.first_name))
    preview_url = load_messages().get("start_preview_url", "").strip()

    if preview_url:
        # Prepend zero-width space hyperlink — Telegram uses it to pick
        # the preview image while keeping the visible text clean (SayGGBot technique)
        text = f'<a href="{escape(preview_url)}">\u200b</a>{text}'
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=build_inline_keyboard("start"),
            link_preview_options=LinkPreviewOptions(
                is_disabled=False,
                show_above_text=True,
                url=preview_url,
                prefer_large_media=True,
            ),
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=build_inline_keyboard("start"),
        )


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entries = get_history(update.effective_user.id)
    if not entries:
        await update.message.reply_text(
            get_msg("history_empty"), parse_mode="HTML",
            reply_markup=build_inline_keyboard("history_empty"),
        )
        return

    msgs      = load_messages()
    header    = msgs.get("history_header", DEFAULT_MESSAGES["history_header"])
    item_tmpl = msgs.get("history_item",   DEFAULT_MESSAGES["history_item"])

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
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=build_inline_keyboard("history_header"),
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("⛔ <b>Owner only.</b>", parse_mode="HTML")
        return

    s     = load_stats()
    today = _today_str()
    now   = datetime.now(timezone.utc)

    # ── Uptime ──────────────────────────────────────────────────
    delta   = now - BOT_START_TIME
    udays   = delta.days
    uhours  = delta.seconds // 3600
    umins   = (delta.seconds % 3600) // 60
    uparts  = []
    if udays:   uparts.append(f"{udays}d")
    if uhours:  uparts.append(f"{uhours}h")
    uparts.append(f"{umins}m")
    uptime  = " ".join(uparts) or "< 1m"

    # ── Headline counters ───────────────────────────────────────
    total_lookups = s.get("total_lookups", 0)
    total_found   = s.get("total_found", 0)
    total_nf      = s.get("total_not_found", 0)
    total_cf      = s.get("total_conn_failed", 0)
    total_users   = len(s.get("user_activity", {}))
    rate          = f"{total_found / total_lookups * 100:.1f}%" if total_lookups else "—"

    # ── Time-window counters ────────────────────────────────────
    daily         = s.get("daily", {})
    today_data    = daily.get(today, {})
    today_count   = today_data.get("lookups", 0)
    today_found   = today_data.get("found", 0)
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday     = daily.get(yesterday_str, {}).get("lookups", 0)
    today_dt      = now.date()
    week_start    = today_dt - timedelta(days=today_dt.weekday())
    week_count    = sum(
        daily.get((week_start + timedelta(days=i)).strftime("%Y-%m-%d"), {}).get("lookups", 0)
        for i in range(7)
    )
    month_prefix  = now.strftime("%Y-%m")
    month_count   = sum(
        v.get("lookups", 0) for k, v in daily.items() if k.startswith(month_prefix)
    )

    # ── 7-day bar chart ─────────────────────────────────────────
    BARS      = " ▁▂▃▄▅▆▇█"
    day_vals  = [
        daily.get((today_dt - timedelta(days=6 - i)).strftime("%Y-%m-%d"), {}).get("lookups", 0)
        for i in range(7)
    ]
    max_val   = max(day_vals) or 1
    chart_lines = []
    for i, cnt in enumerate(day_vals):
        day_dt  = today_dt - timedelta(days=6 - i)
        bar_idx = round(cnt / max_val * 8)
        bar_idx = max(1, bar_idx) if cnt > 0 else 0
        bar     = BARS[bar_idx] * 10
        label   = day_dt.strftime("%d %b")
        marker  = "  ← today" if day_dt == today_dt else ""
        chart_lines.append(f"  <code>{label}</code>  {bar}  <b>{cnt}</b>{marker}")

    # ── Top 5 UIDs ──────────────────────────────────────────────
    MEDALS   = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    uid_rows = sorted(
        s.get("uid_counts", {}).items(),
        key=lambda x: x[1].get("count", 0), reverse=True
    )[:5]
    uid_lines = [
        f"  {MEDALS[i]}  <code>{escape(d.get('last_username','?'))}</code>"
        f"  <code>{escape(uid)}</code>  <b>{d.get('count',0)}×</b>"
        for i, (uid, d) in enumerate(uid_rows)
    ] or ["  <i>No data yet.</i>"]

    # ── Top 5 users ─────────────────────────────────────────────
    user_rows = sorted(
        s.get("user_activity", {}).items(),
        key=lambda x: x[1].get("count", 0), reverse=True
    )[:5]
    user_lines = [
        f"  {MEDALS[i]}  <b>{escape(d.get('first_name','?'))}</b>"
        f"  —  {d.get('count',0)} lookups"
        f"  <i>(since {d.get('first_seen','?')})</i>"
        for i, (_, d) in enumerate(user_rows)
    ] or ["  <i>No data yet.</i>"]

    # ── Peak day ────────────────────────────────────────────────
    if daily:
        peak_date, peak_data = max(daily.items(), key=lambda x: x[1].get("lookups", 0))
        peak_str = f"{peak_date}  ({peak_data.get('lookups', 0)} lookups)"
    else:
        peak_str = "—"

    text = (
        "📊 <b>Bot Statistics</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥  <b>Total Users</b>          {total_users}\n"
        f"🔍  <b>All-Time Lookups</b>     {total_lookups}\n"
        f"✅  <b>Found</b>               {total_found}  <i>({rate} success)</i>\n"
        f"💀  <b>Not Found</b>           {total_nf}\n"
        f"❌  <b>Conn. Failures</b>      {total_cf}\n"
        f"⏱️  <b>Uptime</b>              {uptime}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📅  <b>Today</b>               {today_count}  <i>({today_found} found)</i>\n"
        f"🗓️  <b>Yesterday</b>           {yesterday}\n"
        f"📈  <b>This Week</b>           {week_count}\n"
        f"🗃️  <b>This Month</b>          {month_count}\n"
        f"🏔️  <b>Peak Day</b>            {peak_str}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📉 <b>Last 7 Days</b>\n"
        + "\n".join(chart_lines) + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🏆 <b>Top 5 Most Searched UIDs</b>\n"
        + "\n".join(uid_lines) + "\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👤 <b>Top 5 Most Active Users</b>\n"
        + "\n".join(user_lines) + "\n\n"
        f"<i>🕐 {now.strftime('%Y-%m-%d %H:%M')} UTC</i>"
    )

    await update.message.reply_text(text, parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes to settings wizard or UID lookup."""
    ud      = context.user_data
    bs_step = ud.get("bs_step", BS_IDLE)

    if is_owner(update):
        if bs_step == BS_EDIT_TEXT:
            await _bs_save_text(update, context)
            return
        if bs_step == BS_BTN_TEXT:
            await _bs_save_btn_text(update, context)
            return
        if bs_step == BS_BTN_URL:
            await _bs_save_btn_url(update, context)
            return
        if bs_step == BS_BTN_EMOJI:
            await _bs_save_btn_emoji(update, context)
            return

    await _lookup_uid(update, context)


async def _lookup_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text      = update.message.text.strip()
    tg_user   = update.effective_user
    user_id   = tg_user.id
    fn        = (tg_user.first_name or "").strip() or "Unknown"

    if not is_valid_uid(text):
        await update.message.reply_text(
            get_msg("invalid_uid"),
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
            reply_markup=build_inline_keyboard("invalid_uid"),
        )
        return

    wait = await update.message.reply_text(
        get_msg("searching"),
        parse_mode="HTML",
        reply_to_message_id=update.message.message_id,
    )
    username, api_status = get_bgmi_username(text)
    await wait.delete()

    if api_status == "success":
        add_to_history(user_id, text, username)
        record_lookup(user_id, text, username, fn, "found")
        await update.message.reply_text(
            get_msg("found",
                    username=escape(username),
                    uid=escape(text),
                    server="BGMI — India"),
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
            reply_markup=build_inline_keyboard("found"),
        )
    elif api_status == "token_failed":
        record_lookup(user_id, text, None, fn, "conn_failed")
        await update.message.reply_text(
            get_msg("conn_failed"),
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
            reply_markup=build_inline_keyboard("conn_failed"),
        )
    else:
        record_lookup(user_id, text, None, fn, "not_found")
        await update.message.reply_text(
            get_msg("not_found", uid=escape(text)),
            parse_mode="HTML",
            reply_to_message_id=update.message.message_id,
            reply_markup=build_inline_keyboard("not_found"),
        )


# ─────────────────────────────────────────────────────────────
# HANDLERS — OWNER /botsettings
# ─────────────────────────────────────────────────────────────

async def cmd_botsettings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("⛔ <b>Owner only.</b>", parse_mode="HTML")
        return
    _bs_clear(context.user_data)
    await update.message.reply_text(
        "🛠 <b>Bot Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Tap any reply to <b>view, edit text, or manage buttons</b>.\n"
        "Changes go live <b>instantly</b> for all users.\n\n"
        "<i>🔘N = number of inline buttons configured.</i>",
        parse_mode="HTML",
        reply_markup=_main_menu_keyboard(),
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    ud      = context.user_data
    bs_step = ud.get("bs_step", BS_IDLE)
    bs_key  = ud.get("bs_key")

    if bs_step in (BS_BTN_TEXT, BS_BTN_URL, BS_BTN_EMOJI):
        ud["bs_step"] = BS_IDLE
        ud.pop("bs_current_btn", None)
        ud.pop("bs_prompt_msg_id", None)
        if bs_key:
            await update.message.reply_text(
                "↩️ <b>Button adding cancelled.</b>",
                parse_mode="HTML",
                reply_markup=_manage_buttons_keyboard(bs_key),
            )
            await update.message.reply_text(
                _manage_buttons_text(bs_key),
                parse_mode="HTML",
                reply_markup=_manage_buttons_keyboard(bs_key),
            )
        return

    _bs_clear(ud)
    await update.message.reply_text(
        "↩️ <b>Cancelled.</b>",
        parse_mode="HTML",
        reply_markup=_main_menu_keyboard(),
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q  = update.callback_query
    ud = context.user_data
    await q.answer()

    if not (OWNER_ID and q.from_user.id == OWNER_ID):
        await q.answer("⛔ Owner only.", show_alert=True)
        return

    data = q.data or ""

    # ── No-op (display-only buttons) ──
    if data == "s_noop":
        return

    # ── Close ──
    if data == "s_close":
        _bs_clear(ud)
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    # ── Back to main menu ──
    if data == "s_back":
        _bs_clear(ud)
        try:
            await q.message.edit_text(
                "🛠 <b>Bot Settings</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Tap any reply to <b>view, edit text, or manage buttons</b>.\n"
                "Changes go live <b>instantly</b> for all users.\n\n"
                "<i>🔘N = number of inline buttons configured.</i>",
                parse_mode="HTML",
                reply_markup=_main_menu_keyboard(),
            )
        except Exception:
            pass
        return

    # ── View panel ──
    m = re.match(r"^s_v_(.+)$", data)
    if m:
        key = m.group(1)
        if key not in MSG_META:
            return
        _bs_clear(ud)
        try:
            await q.message.edit_text(
                _view_panel_text(key),
                parse_mode="HTML",
                reply_markup=_view_panel_keyboard(key),
            )
        except Exception:
            pass
        return

    # ── Edit text / Set URL ──
    m = re.match(r"^s_et_(.+)$", data)
    if m:
        key = m.group(1)
        if key not in MSG_META:
            return
        label, vars_list = MSG_META[key]

        ud["bs_step"] = BS_EDIT_TEXT
        ud["bs_key"]  = key

        if key in PLAIN_KEYS:
            current_url = load_messages().get(key, "").strip()
            cur_line    = (
                f"\n📌 <b>Current URL:</b>\n<code>{escape(current_url)}</code>\n"
                if current_url else
                "\n📌 <b>Current URL:</b>  <i>Not set</i>\n"
            )
            prompt_text = (
                f"🖼️ <b>Set Start Image URL</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Reply with the <b>image URL</b> to display above /start.\n\n"
                "<b>How it works:</b>\n"
                "Telegram fetches the link preview for the URL you provide\n"
                "and shows it as a large image <b>above the /start caption</b>.\n\n"
                "<b>Best URLs to use:</b>\n"
                "  • Direct image: <code>https://example.com/banner.jpg</code>\n"
                "  • Telegraph article with a banner image\n"
                "  • Any page Telegram can generate a preview from\n"
                f"{cur_line}\n"
                "Reply with the new URL, or send /cancel to go back."
            )
            prompt = await q.message.reply_text(
                prompt_text,
                parse_mode="HTML",
                reply_markup=ForceReply(
                    selective=True,
                    input_field_placeholder="Paste image URL here…"
                ),
            )
            ud["bs_prompt_msg_id"] = prompt.message_id
            return

        var_guide = ""
        if vars_list:
            var_guide = (
                "\n📌 <b>Available variables:</b>\n"
                + "  ".join(f"<code>{v}</code>" for v in vars_list)
                + "\n\n"
                "<b>Variable usage example:</b>\n"
                "<code>Hey {mention}, welcome to BGMI Lookup!</code>\n\n"
            )

        prompt_text = (
            f"✏️ <b>Editing: {label}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>Supported formatting:</b>\n"
            "  • <b>bold</b>  •  <i>italic</i>  •  <u>underline</u>\n"
            "  • <s>strikethrough</s>  •  <tg-spoiler>spoiler</tg-spoiler>\n"
            "  • <code>inline code</code>  •  hyperlinks\n"
            "  • Custom emoji (animated sticker emoji)\n\n"
            f"{var_guide}"
            "Reply to this message with your new text.\n"
            "Send /cancel to go back."
        )
        prompt = await q.message.reply_text(
            prompt_text,
            parse_mode="HTML",
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder=f"New text for {label}…"
            ),
        )
        ud["bs_prompt_msg_id"] = prompt.message_id
        return

    # ── Manage buttons panel ──
    m = re.match(r"^s_mb_(.+)$", data)
    if m:
        key = m.group(1)
        if key not in MSG_META:
            return
        _bs_clear(ud)
        try:
            await q.message.edit_text(
                _manage_buttons_text(key),
                parse_mode="HTML",
                reply_markup=_manage_buttons_keyboard(key),
            )
        except Exception:
            pass
        return

    # ── Delete button #idx ──
    m = re.match(r"^s_db_(.+?)_(\d+)$", data)
    if m:
        key = m.group(1)
        idx = int(m.group(2))
        if key not in MSG_META:
            return
        buttons = load_buttons(key)
        if 0 <= idx < len(buttons):
            buttons.pop(idx)
            save_buttons(key, buttons)
        try:
            await q.message.edit_text(
                _manage_buttons_text(key),
                parse_mode="HTML",
                reply_markup=_manage_buttons_keyboard(key),
            )
        except Exception:
            pass
        return

    # ── Add button — start builder ──
    m = re.match(r"^s_ab_(.+)$", data)
    if m:
        key = m.group(1)
        if key not in MSG_META:
            return
        if len(load_buttons(key)) >= 10:
            await q.answer("⚠️ Maximum 10 buttons per reply.", show_alert=True)
            return
        ud["bs_step"]       = BS_BTN_TEXT
        ud["bs_key"]        = key
        ud["bs_current_btn"] = {}

        label, _ = MSG_META[key]
        btn_num  = len(load_buttons(key)) + 1
        prompt = await q.message.reply_text(
            f"🔘 <b>Button Builder — {label}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Step 1 of 3 — Button Label (Button #{btn_num})</b>\n\n"
            "Reply with the <b>visible text</b> for this button.\n\n"
            "<b>Examples:</b>\n"
            "<code>Join Channel</code>\n"
            "<code>📢 Our Channel</code>\n"
            "<code>Visit Website</code>\n\n"
            "<i>Send /cancel to abort.</i>",
            parse_mode="HTML",
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder="Type the button label…"
            ),
        )
        ud["bs_prompt_msg_id"] = prompt.message_id
        return

    # ── Button style chosen ──
    m = re.match(r"^s_bs_(.+)$", data)
    if m:
        style = m.group(1)
        if style not in STYLE_OPTIONS:
            style = "default"

        ud.get("bs_current_btn", {})
        if "bs_current_btn" not in ud:
            return

        ud["bs_current_btn"]["style"] = style if style != "default" else ""
        key   = ud.get("bs_key", "")
        label = MSG_META.get(key, ("Message", []))[0]

        ud["bs_step"] = BS_BTN_EMOJI
        btn_text = ud["bs_current_btn"].get("text", "")
        btn_url  = ud["bs_current_btn"].get("url", "")

        prompt = await q.message.reply_text(
            f"🔘 <b>Button Builder — {label}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>Step 3 of 3 — Custom Emoji Icon (optional)</b>\n\n"
            f"Button: <b>{escape(btn_text)}</b>\n"
            f"URL: <code>{escape(btn_url)}</code>\n"
            f"Style: <b>{STYLE_LABEL.get(style, style)}</b>\n\n"
            "Send a <b>custom emoji ID</b> to add an icon to the button,\n"
            "or tap <b>Skip</b> to finish without an icon.\n\n"
            "<b>How to get emoji ID:</b>\n"
            "Send the custom emoji in Saved Messages, forward to\n"
            "@getidsbot and copy the numeric ID.\n\n"
            "<b>Example ID:</b>\n"
            "<code>5368324170671202286</code>\n\n"
            "<i>Send /cancel to abort.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip — No Icon", callback_data="s_se")],
            ]),
        )
        ud["bs_prompt_msg_id"] = prompt.message_id
        return

    # ── Skip emoji ──
    if data == "s_se":
        ud.get("bs_current_btn", {})
        if "bs_current_btn" not in ud:
            return
        ud["bs_current_btn"]["icon_custom_emoji_id"] = ""
        await _bs_finish_button(q.message.chat_id, context)
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    # ── Preview message ──
    m = re.match(r"^s_pv_(.+)$", data)
    if m:
        key = m.group(1)
        if key not in MSG_META:
            return
        await _bs_send_preview(q.message.chat_id, key, context)
        return

    # ── Reset text to default ──
    m = re.match(r"^s_rt_(.+)$", data)
    if m:
        key = m.group(1)
        if key not in MSG_META:
            return
        msgs = load_messages()
        msgs.pop(key, None)
        save_messages(msgs)
        try:
            await q.message.edit_text(
                _view_panel_text(key),
                parse_mode="HTML",
                reply_markup=_view_panel_keyboard(key),
            )
        except Exception:
            pass
        await q.answer("✅ Text reset to default.", show_alert=False)
        return

    # ── Reset (clear) all buttons ──
    m = re.match(r"^s_rb_(.+)$", data)
    if m:
        key = m.group(1)
        if key not in MSG_META:
            return
        save_buttons(key, [])
        try:
            await q.message.edit_text(
                _view_panel_text(key),
                parse_mode="HTML",
                reply_markup=_view_panel_keyboard(key),
            )
        except Exception:
            pass
        await q.answer("🗑️ All buttons cleared.", show_alert=False)
        return


# ─────────────────────────────────────────────────────────────
# BOTSETTINGS — message capture handlers
# ─────────────────────────────────────────────────────────────

async def _bs_save_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud  = context.user_data
    key = ud.get("bs_key")
    if not key:
        _bs_clear(ud)
        return

    label, _ = MSG_META.get(key, (key, []))

    # Plain keys (e.g. URL fields) — save raw text, no HTML capture
    if key in PLAIN_KEYS:
        new_val = (update.message.text or "").strip()
        if not re.match(r"^https?://", new_val):
            await update.message.reply_text(
                "⚠️ <b>Invalid URL</b>\n\n"
                "Must start with <code>https://</code> or <code>http://</code>\n\n"
                "Please reply with a valid image URL, or send /cancel.",
                parse_mode="HTML",
            )
            return
        msgs = load_messages()
        msgs[key] = new_val
        save_messages(msgs)
        _bs_clear(ud)
        await update.message.reply_text(
            f"✅ <b>Start Image URL saved!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🖼️ <code>{escape(new_val)}</code>\n\n"
            "The image will now appear <b>above</b> the /start caption\n"
            "as a large link preview for all users.\n\n"
            "💾 <i>Changes are live instantly.</i>",
            parse_mode="HTML",
            reply_markup=_view_panel_keyboard(key),
        )
        return

    new_text = _capture_html(update.message)
    msgs = load_messages()
    msgs[key] = new_text
    save_messages(msgs)

    # Build preview with sample values
    try:
        preview = new_text.format(**SAMPLE)
    except KeyError:
        preview = new_text

    _bs_clear(ud)

    await update.message.reply_text(
        f"✅ <b>{label}</b> updated!\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📄 <b>Preview:</b>\n"
        f"<blockquote>{preview}</blockquote>\n\n"
        "💾 <i>Changes are live instantly.</i>",
        parse_mode="HTML",
        reply_markup=_view_panel_keyboard(key),
    )
    # Also send the updated view panel
    await update.message.reply_text(
        _view_panel_text(key),
        parse_mode="HTML",
        reply_markup=_view_panel_keyboard(key),
    )


async def _bs_save_btn_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud   = context.user_data
    key  = ud.get("bs_key", "")
    text = (update.message.text or "").strip()

    if not text:
        await update.message.reply_text(
            "⚠️ Button label cannot be empty. Please reply with a button label.",
            parse_mode="HTML",
        )
        return

    ud.setdefault("bs_current_btn", {})["text"] = text
    ud["bs_step"] = BS_BTN_URL

    label = MSG_META.get(key, ("Message", []))[0]
    prompt = await update.message.reply_text(
        f"🔘 <b>Button Builder — {label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Step 2 of 3 — Destination URL</b>\n\n"
        f"Button label: <b>{escape(text)}</b>\n\n"
        "Reply with the <b>URL</b> this button should open.\n\n"
        "<b>Accepted formats:</b>\n"
        "<code>https://t.me/yourchannel</code>\n"
        "<code>https://example.com/page</code>\n"
        "<code>tg://resolve?domain=username</code>\n\n"
        "<i>Send /cancel to abort.</i>",
        parse_mode="HTML",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Paste the button URL…"
        ),
    )
    ud["bs_prompt_msg_id"] = prompt.message_id


async def _bs_save_btn_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud  = context.user_data
    key = ud.get("bs_key", "")
    url = (update.message.text or "").strip()

    if not re.match(r"^(https?://|tg://)", url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL</b>\n\n"
            "Must start with <code>https://</code>, <code>http://</code>, or <code>tg://</code>\n\n"
            "<b>Example:</b>\n"
            "<code>https://t.me/yourchannel</code>\n\n"
            "Please reply with a valid URL.",
            parse_mode="HTML",
        )
        return

    ud.setdefault("bs_current_btn", {})["url"] = url
    ud["bs_step"] = BS_BTN_EMOJI  # will go to style selection first

    label    = MSG_META.get(key, ("Message", []))[0]
    btn_text = ud["bs_current_btn"].get("text", "")

    await update.message.reply_text(
        f"🔘 <b>Button Builder — {label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Step 3 of 3 — Button Style</b>\n\n"
        f"Button: <b>{escape(btn_text)}</b>\n"
        f"URL: <code>{escape(url)}</code>\n\n"
        "Choose the <b>button style</b> (colour):\n\n"
        "  <b>⬜ Default</b>  —  Standard style\n"
        "  <b>🔵 Primary</b>  —  Blue accent\n"
        "  <b>🟢 Success</b>  —  Green accent\n"
        "  <b>🔴 Danger</b>   —  Red accent\n\n"
        "<i>Note: Button colours are visible in supported Telegram clients.</i>",
        parse_mode="HTML",
        reply_markup=_style_keyboard(),
    )
    # Reset step so style pick is handled by callback, not message handler
    ud["bs_step"] = BS_IDLE


async def _bs_save_btn_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud       = context.user_data
    emoji_id = (update.message.text or "").strip()

    if not re.match(r"^\d{5,32}$", emoji_id):
        await update.message.reply_text(
            "⚠️ <b>Invalid custom emoji ID</b>\n\n"
            "Must be a numeric ID (5–32 digits).\n\n"
            "<b>Example:</b> <code>5368324170671202286</code>\n\n"
            "Send a valid ID, or tap <b>Skip — No Icon</b>.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip — No Icon", callback_data="s_se")],
            ]),
        )
        return

    ud.setdefault("bs_current_btn", {})["icon_custom_emoji_id"] = emoji_id
    await _bs_finish_button(update.message.chat_id, context)


async def _bs_finish_button(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    ud  = context.user_data
    key = ud.get("bs_key", "")
    btn = dict(ud.get("bs_current_btn", {}))

    if btn.get("text") and btn.get("url"):
        buttons = load_buttons(key)
        buttons.append(btn)
        save_buttons(key, buttons)

    ud.pop("bs_current_btn", None)
    ud["bs_step"] = BS_IDLE

    label   = MSG_META.get(key, ("Message", []))[0]
    buttons = load_buttons(key)

    style_name = STYLE_LABEL.get(btn.get("style") or "default", "Default")
    has_emoji  = "✅" if btn.get("icon_custom_emoji_id") else "❌"

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ <b>Button Added — {label}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Label:</b>  {escape(btn.get('text', ''))}\n"
            f"<b>URL:</b>    <code>{escape(btn.get('url', ''))}</code>\n"
            f"<b>Style:</b>  {style_name}\n"
            f"<b>Emoji:</b>  {has_emoji}\n\n"
            f"<b>All buttons for this reply ({len(buttons)}):</b>\n"
            f"{_buttons_summary(buttons)}\n\n"
            "💾 <i>Changes are live instantly.</i>"
        ),
        parse_mode="HTML",
        reply_markup=_manage_buttons_keyboard(key),
    )


async def _bs_send_preview(chat_id: int, key: str, context: ContextTypes.DEFAULT_TYPE):
    label, vars_list = MSG_META[key]
    tmpl   = load_messages().get(key, DEFAULT_MESSAGES.get(key, ""))
    markup = build_inline_keyboard(key)

    try:
        rendered = tmpl.format(**SAMPLE)
    except KeyError:
        rendered = tmpl

    header = (
        f"👁️ <b>Preview: {label}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    if vars_list:
        header += (
            "<i>Sample values used:</i>  "
            + "  ".join(f"<code>{v}</code>" for v in vars_list)
            + "\n\n"
        )

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=header + rendered,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ <b>Preview render error</b>\n<code>{escape(str(e)[:300])}</code>",
            parse_mode="HTML",
        )


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

    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("history",     cmd_history))
    app.add_handler(CommandHandler("stats",       cmd_stats))
    app.add_handler(CommandHandler("botsettings", cmd_botsettings))
    app.add_handler(CommandHandler("cancel",      cmd_cancel))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^s_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("[✓] BGMI ID INFO Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
