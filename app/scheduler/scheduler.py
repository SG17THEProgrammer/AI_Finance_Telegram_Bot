"""
Proactive daily briefings. Runs a check every minute; any onboarded user
whose briefing_time (HH:MM, IST) matches the current time - and who hasn't
already gotten today's briefing - gets a proactive message sent to them,
unprompted, exactly per the brief's "Daily Intelligence" requirement.
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import or_

from app.database.db import SessionLocal, User, AllowedUser
from app.config import OWNER_TELEGRAM_IDS
from app.services.llm import get_reply
from app.bot.handlers import build_profile_summary, _to_telegram_markdown

IST = ZoneInfo("Asia/Kolkata")

BRIEFING_TRIGGER_PROMPT = (
    "[This is an automated daily briefing trigger, not a message from the user - "
    "generate their proactive morning briefing now.] Give the user a concise daily "
    "market briefing. If they have followed sectors/watchlist, personalize to that: "
    "what's moved, any notable news, anything genuinely worth their attention today. "
    "If they have NO watchlist/sectors saved yet, give a brief general market snapshot "
    "instead (major indices, 1-2 significant headlines) - never send nothing just "
    "because there's no personalization yet. Use your tools to pull real current data "
    "- don't guess. Keep it short and scannable, same style as always."
)


async def _send_briefing(bot, user_id: int):
    db = SessionLocal()
    try:
        user = db.query(User).join(AllowedUser, AllowedUser.telegram_id == User.telegram_id).filter(User.id == user_id).first()
        if not user:
            return

        profile_summary = build_profile_summary(user)
        try:
            reply_text = get_reply(db, user.telegram_id, [], BRIEFING_TRIGGER_PROMPT, profile_summary)
        except Exception as exc:
            print(f"[Briefing generation error for {user.telegram_id}] {type(exc).__name__}: {exc}")
            return

        try:
            formatted = _to_telegram_markdown(reply_text)
            await bot.send_message(chat_id=int(user.telegram_id), text=formatted, parse_mode="Markdown")
        except Exception:
            try:
                await bot.send_message(chat_id=int(user.telegram_id), text=reply_text)
            except Exception as exc:
                print(f"[Briefing send error for {user.telegram_id}] {type(exc).__name__}: {exc}")
                return

        user.last_briefing_date = datetime.now(IST).strftime("%Y-%m-%d")
        db.commit()
    finally:
        db.close()


async def _check_and_send_briefings(bot):
    now = datetime.now(IST)
    current_time_str = now.strftime("%H:%M")
    today_str = now.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        candidates = (
            db.query(User)
            .filter(
                User.briefing_time.isnot(None),
                User.briefing_time == current_time_str,
                or_(User.last_briefing_date.is_(None), User.last_briefing_date != today_str),
            )
            .all()
        )
        candidates = [
            u for u in candidates
            if u.telegram_id in OWNER_TELEGRAM_IDS
            or db.query(AllowedUser).filter(AllowedUser.telegram_id == u.telegram_id).first() is not None
        ]
        user_ids = [u.id for u in candidates]
    finally:
        db.close()

    for user_id in user_ids:
        await _send_briefing(bot, user_id)


def start_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone=IST)
    scheduler.add_job(_check_and_send_briefings, "interval", minutes=1, args=[bot])
    scheduler.start()
    print("[Scheduler] Daily briefing check started (every 1 min, IST).")
    return scheduler