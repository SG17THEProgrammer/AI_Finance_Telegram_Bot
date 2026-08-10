
"""Invite-only Telegram access control. Runtime allowlist is stored in SQLite."""
from datetime import datetime, timezone
from telegram.error import BadRequest, TelegramError
from app.config import OWNER_TELEGRAM_IDS, OWNER_TELEGRAM_USERNAMES, ALLOWED_TELEGRAM_USERNAMES
from app.db import AllowedUser

def _norm_username(username):
    if not username:
        return None

    return username.lstrip("@").strip().lower() or None


def is_owner(update):
    """Check whether the current Telegram user is the bot owner."""
    user = update.effective_user

    if not user:
        return False

    telegram_id = str(user.id)
    username = _norm_username(user.username)

    return (
        telegram_id in OWNER_TELEGRAM_IDS
        or (
            username is not None
            and username in OWNER_TELEGRAM_USERNAMES
        )
    )


def is_allowed(update, db):
    """
    Check whether the current user is allowed.

    We support:
    1. Owner Telegram ID
    2. Owner username
    3. Previously stored allowed Telegram ID
    4. Configured allowed username
    """

    user = update.effective_user

    if not user:
        return False

    telegram_id = str(user.id)
    username = _norm_username(user.username)

    # Owner by ID
    if telegram_id in OWNER_TELEGRAM_IDS:
        return True

    # Owner by username
    if username and username in OWNER_TELEGRAM_USERNAMES:
        return True

    # Previously stored allowed Telegram ID
    existing = (
        db.query(AllowedUser)
        .filter(AllowedUser.telegram_id == telegram_id)
        .first()
    )

    if existing:
        return True

    # Configured judge / invited username
    if username and username in ALLOWED_TELEGRAM_USERNAMES:
        return True

    return False


def record_allowed_user(db, update):
    """
    Persist an allowed user's Telegram ID and current username.
    This means future checks can use the stable Telegram ID.
    """

    user = update.effective_user

    if not user:
        return

    telegram_id = str(user.id)
    username = _norm_username(user.username)

    row = (
        db.query(AllowedUser)
        .filter(AllowedUser.telegram_id == telegram_id)
        .first()
    )

    if row:
        row.username = username
        row.first_name = user.first_name
        db.commit()
        return

    is_owner_user = int(
        telegram_id in OWNER_TELEGRAM_IDS
        or (
            username
            and username in OWNER_TELEGRAM_USERNAMES
        )
    )

    db.add(
        AllowedUser(
            telegram_id=telegram_id,
            username=username,
            first_name=user.first_name,
            is_owner=is_owner_user,
            added_at=datetime.now(timezone.utc),
        )
    )

    db.commit()


def _target(token):
    token = token.strip()

    if token.isdigit():
        return "id", token

    username = _norm_username(token)

    if username:
        return "username", username

    raise ValueError(
        "Use /allow @username or /allow 123456789."
    )


def async_placeholder():
    pass

async def resolve_target(bot, token):
    kind, value = _target(token)
    if kind == "id":
        return value, None
    try:
        chat = await bot.get_chat("@" + value)
    except (BadRequest, TelegramError) as exc:
        raise ValueError(
            f"I couldn't find @{value}. Use the exact public username or numeric Telegram ID."
        ) from exc
    return str(chat.id), getattr(chat, "username", None)

async def allow_target(db, bot, token):
    tid, username = await resolve_target(bot, token)
    row = db.query(AllowedUser).filter(AllowedUser.telegram_id == tid).first()
    if row:
        row.username = username or row.username
        db.commit()
        return tid, row.username, False
    db.add(AllowedUser(
        telegram_id=tid, username=username, is_owner=0,
        added_at=datetime.now(timezone.utc)
    ))
    db.commit()
    return tid, username, True

async def remove_target(db, bot, token):
    tid, username = await resolve_target(bot, token)
    if tid in OWNER_TELEGRAM_IDS:
        raise ValueError("I won't remove a configured owner.")
    row = db.query(AllowedUser).filter(AllowedUser.telegram_id == tid).first()
    if not row:
        return tid, username, False
    if row.is_owner:
        raise ValueError("I won't remove an owner.")
    db.delete(row)
    db.commit()
    return tid, row.username or username, True

def allowed_list(db):
    return db.query(AllowedUser).order_by(
        AllowedUser.is_owner.desc(), AllowedUser.id.asc()
    ).all()
