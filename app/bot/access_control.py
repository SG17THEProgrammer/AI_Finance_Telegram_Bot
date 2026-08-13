"""Invite-only Telegram access control. Runtime allowlist is stored in SQLite."""
from datetime import datetime, timezone
from app.config import OWNER_TELEGRAM_IDS, OWNER_TELEGRAM_USERNAMES
from app.db import AllowedUser

def _norm_username(username):
    if not username:
        return None
    return username.lstrip("@").strip() or None


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
    """
    user = update.effective_user
    if not user:
        return False

    telegram_id = str(user.id)
    username = _norm_username(user.username)

    # 1. Owner checks
    if telegram_id in OWNER_TELEGRAM_IDS:
        return True
    if username and username in OWNER_TELEGRAM_USERNAMES:
        return True

    # 2. Previously stored allowed Telegram ID
    existing = db.query(AllowedUser).filter(AllowedUser.telegram_id == telegram_id).first()
    if existing:
        return True

    # 3. NEW: Check if there is a pending manual /allow for this username
    if username:
        pending = db.query(AllowedUser).filter(AllowedUser.username == username).first()
        if pending:
            return True

    # 4. Configured judge / invited username from .env
    # if username and username in ALLOWED_TELEGRAM_USERNAMES:
    #     return True

    return False


def record_allowed_user(db, update):
    """
    Persist an allowed user's Telegram ID and current username.
    Upgrades 'pending' usernames to real Telegram IDs.
    """
    user = update.effective_user
    if not user:
        return

    telegram_id = str(user.id)
    username = _norm_username(user.username)

    # 1. Update if the real ID already exists
    row = db.query(AllowedUser).filter(AllowedUser.telegram_id == telegram_id).first()
    if row:
        row.username = username
        row.first_name = user.first_name
        db.commit()
        return

    # 2. NEW: Upgrade a pending username to a real ID
    if username:
        pending = db.query(AllowedUser).filter(AllowedUser.username == username).first()
        if pending:
            pending.telegram_id = telegram_id
            pending.first_name = user.first_name
            db.commit()
            return

    # 3. Create a brand new record
    is_owner_user = int(
        telegram_id in OWNER_TELEGRAM_IDS
        or (username and username in OWNER_TELEGRAM_USERNAMES)
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
    raise ValueError("Use /allow @username or /allow 123456789.")


async def allow_target(db, bot, token):
    """Bypasses Telegram API and stores directly to the DB"""
    kind, value = _target(token)
    
    if kind == "id":
        tid = value
        username = None
        row = db.query(AllowedUser).filter(AllowedUser.telegram_id == tid).first()
    else:
        # Create a placeholder ID for new usernames
        tid = f"pending_{value}" 
        username = value
        row = db.query(AllowedUser).filter(AllowedUser.username == username).first()

    if row:
        return row.telegram_id, row.username, False # Already allowed

    db.add(AllowedUser(
        telegram_id=tid, username=username, is_owner=0,
        added_at=datetime.now(timezone.utc)
    ))
    db.commit()
    return tid, username, True


async def remove_target(db, bot, token):
    """Looks up user directly in the local DB instead of Telegram API"""
    kind, value = _target(token)
    
    if kind == "id":
        row = db.query(AllowedUser).filter(AllowedUser.telegram_id == value).first()
    else:
        row = db.query(AllowedUser).filter(AllowedUser.username == value).first()

    if not row:
        return value, value, False

    if row.telegram_id in OWNER_TELEGRAM_IDS or row.is_owner:
        raise ValueError("I won't remove an owner.")

    db.delete(row)
    db.commit()
    return row.telegram_id, row.username, True


def allowed_list(db):
    return db.query(AllowedUser).order_by(
        AllowedUser.is_owner.desc(), AllowedUser.id.asc()
    ).all()