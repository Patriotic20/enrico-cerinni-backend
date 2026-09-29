"""Integration credentials (Eskiz SMS, Telegram bot) managed from the UI.

Values live in the app_settings table, Fernet-encrypted with a key derived
from SECRET_KEY, and are never returned by the API — only whether they are
set. A value saved in the UI overrides the matching NOTIFICATION__* env var;
clearing it falls back to the env var again.

The effective values are overlaid onto `settings.notification`, so the SMS and
Telegram senders keep reading config from one place. `apply()` runs per
marketing request (one tiny query), which keeps every worker process in sync.
"""

import base64
import hashlib
import logging
import re
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import PLACEHOLDER_SECRETS, settings
from app.models.app_setting import AppSetting
from app.services.eskiz_sms import DEFAULT_SENDER, eskiz_client

logger = logging.getLogger(__name__)

FIELDS = ("telegram_bot_token", "eskiz_email", "eskiz_password", "sms_from_number")
SECRET_FIELDS = {"telegram_bot_token", "eskiz_password"}

# Env values captured at import, so clearing a UI value restores them.
_ENV_DEFAULTS: Dict[str, Optional[str]] = {f: getattr(settings.notification, f) for f in FIELDS}

TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SettingsError(ValueError):
    pass


def _fernet() -> Fernet:
    secret = settings.security.secret_key
    if not secret or secret in PLACEHOLDER_SECRETS:
        raise SettingsError(
            "SECRET_KEY serverda sozlanmagan — maxfiy ma'lumotlarni xavfsiz saqlab bo'lmaydi"
        )
    digest = hashlib.sha256(b"enrico-app-settings:" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _stored(db: Session) -> Dict[str, str]:
    rows = db.query(AppSetting).filter(AppSetting.key.in_(FIELDS)).all()
    if not rows:
        return {}
    try:
        f = _fernet()
    except SettingsError:
        logger.warning("app_settings present but SECRET_KEY is a placeholder; ignoring them")
        return {}
    values = {}
    for row in rows:
        try:
            values[row.key] = f.decrypt(row.value.encode()).decode()
        except InvalidToken:
            # SECRET_KEY was rotated; the value must be re-entered in the UI.
            logger.warning("Cannot decrypt app setting %s (SECRET_KEY changed?)", row.key)
    return values


def apply(db: Session) -> Dict[str, str]:
    """Overlay stored values onto settings.notification; returns the stored ones."""
    stored = _stored(db)
    n = settings.notification
    before = (n.eskiz_email, n.eskiz_password)
    for field in FIELDS:
        setattr(n, field, stored.get(field, _ENV_DEFAULTS[field]))
    if stored.get("eskiz_email") or stored.get("eskiz_password"):
        n.sms_provider = "eskiz"
    if (n.eskiz_email, n.eskiz_password) != before:
        eskiz_client._token = None  # cached token belongs to the old account
    return stored


def public_view(db: Session) -> dict:
    """What the UI may see: never a secret, only whether it is set."""
    stored = apply(db)
    n = settings.notification

    def source(*fields):
        if any(f in stored for f in fields):
            return "platform"
        if any(_ENV_DEFAULTS[f] for f in fields):
            return "env"
        return None

    token = n.telegram_bot_token or ""
    return {
        "telegram": {
            "configured": bool(token),
            "token_hint": f"…{token[-4:]}" if token else None,
            "source": source("telegram_bot_token"),
        },
        "eskiz": {
            "configured": eskiz_client.is_configured(),
            "email": n.eskiz_email,
            "password_set": bool(n.eskiz_password),
            "sender": n.sms_from_number or DEFAULT_SENDER,
            "source": source("eskiz_email", "eskiz_password"),
        },
    }


def _validate(field: str, value: str) -> None:
    if field == "telegram_bot_token" and not TELEGRAM_TOKEN_RE.match(value):
        raise SettingsError("Telegram bot token formati noto'g'ri (masalan 123456:ABC...)")
    if field == "eskiz_email" and not EMAIL_RE.match(value):
        raise SettingsError("Eskiz email noto'g'ri")
    if field == "eskiz_password" and len(value) > 256:
        raise SettingsError("Eskiz parol juda uzun")
    if field == "sms_from_number" and len(value) > 11:
        raise SettingsError("Jo'natuvchi nomi 11 belgidan oshmasligi kerak")


def update(db: Session, changes: Dict[str, Optional[str]], user_id: Optional[int]) -> None:
    """None = keep as is, "" = remove (fall back to env), anything else = save."""
    f = None
    for field, value in changes.items():
        if field not in FIELDS or value is None:
            continue
        value = value.strip()
        row = db.get(AppSetting, field)
        if value == "":
            if row:
                db.delete(row)
            continue
        _validate(field, value)
        f = f or _fernet()
        encrypted = f.encrypt(value.encode()).decode()
        if row:
            row.value, row.updated_by = encrypted, user_id
        else:
            db.add(AppSetting(key=field, value=encrypted, updated_by=user_id))
    db.commit()
    apply(db)
