"""Link clients to the Telegram bot.

A bot can only message users who pressed Start on it, and we need their
chat_id. Each client gets a deep link t.me/<bot>?start=<code>; pressing Start
sends "/start <code>" to the bot, and `sync()` pulls those messages with
getUpdates and stores the chat_id on the matching client.

The code is HMAC-signed so nobody can guess another client's code and
receive their messages. getUpdates works without a public URL (no webhook),
but Telegram keeps undelivered updates only ~24h, so sync runs whenever the
marketing page opens and before each Telegram broadcast.
"""

import hashlib
import hmac
import re
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from app.config import PLACEHOLDER_SECRETS, settings
from app.models.client import Client

API = "https://api.telegram.org/bot{token}/{method}"
START_RE = re.compile(r"^/start\s+c(\d+)_([0-9a-f]{12})$")


def _sign(client_id: int) -> str:
    secret = settings.security.secret_key
    # A public key would let anyone forge every client's link code.
    if not secret or secret in PLACEHOLDER_SECRETS:
        raise RuntimeError("SECRET_KEY serverda sozlanmagan — Telegram havolasini yaratib bo'lmaydi")
    key = secret.encode()
    return hmac.new(key, f"tg-link:{client_id}".encode(), hashlib.sha256).hexdigest()[:12]


def start_code(client_id: int) -> str:
    return f"c{client_id}_{_sign(client_id)}"


async def _call(method: str, **params) -> dict:
    token = settings.notification.telegram_bot_token
    if not token:
        raise RuntimeError("Telegram bot sozlanmagan")
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(API.format(token=token, method=method), json=params)
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or f"Telegram {method} failed")
    return body["result"]


async def link_url(client_id: int) -> str:
    me = await _call("getMe")
    return f"https://t.me/{me['username']}?start={start_code(client_id)}"


async def sync(db: Session) -> List[str]:
    """Apply pending /start messages. Returns names of newly linked clients."""
    updates = await _call("getUpdates", allowed_updates=["message"], timeout=0)
    if not updates:
        return []

    linked: List[str] = []
    replies: List[tuple] = []  # sent after commit, so no DB connection waits on HTTP
    for upd in updates:
        msg = upd.get("message") or {}
        chat_id: Optional[int] = (msg.get("chat") or {}).get("id")
        m = START_RE.match((msg.get("text") or "").strip())
        if not chat_id:
            continue
        if not m or not hmac.compare_digest(m.group(2), _sign(int(m.group(1)))):
            if (msg.get("text") or "").startswith("/start"):
                replies.append((chat_id, "Botga ulanish uchun do'kondan olingan havoladan foydalaning."))
            continue
        client = db.get(Client, int(m.group(1)))
        if not client:
            continue
        if client.telegram_chat_id and client.telegram_chat_id != str(chat_id):
            # Already linked to another chat: a forwarded link must not take it over.
            replies.append((chat_id, "Bu mijoz allaqachon boshqa Telegram hisobiga ulangan."))
            continue
        if client.telegram_chat_id != str(chat_id):
            client.telegram_chat_id = str(chat_id)
            linked.append(f"{client.first_name} {client.last_name}")
        replies.append((chat_id, f"{client.first_name}, siz do'kon xabarlariga muvaffaqiyatli ulandingiz ✅"))
    db.commit()

    for chat_id, text in replies:
        await _reply(chat_id, text)

    # Confirm the processed updates so Telegram does not return them again.
    await _call("getUpdates", offset=updates[-1]["update_id"] + 1, limit=1, timeout=0)
    return linked


async def _reply(chat_id: int, text: str) -> None:
    try:
        await _call("sendMessage", chat_id=chat_id, text=text)
    except Exception:
        pass  # the link itself is what matters
