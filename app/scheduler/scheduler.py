"""
app/scheduler/scheduler.py

Background jobs for Atlas:
  1. Daily briefings       — every 1 min, checks if any user's briefing_time matches now
  2. Alert checker         — every 15 min during market hours
  3. RSI re-armer          — every 15 min alongside alert checker
  4. Daily baseline reset  — 9:16 AM IST weekdays (after NSE open)
  5. API rate monitor      — every 60 min, notifies owner at 50/90/100% usage
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

def _get_briefing_prompt() -> str:
    """Returns a briefing prompt with the correct greeting based on current IST hour."""
    hour = datetime.now(IST).hour
    if 5 <= hour < 12:
        greeting = "Good morning"
    elif 12 <= hour < 17:
        greeting = "Good afternoon"
    elif 17 <= hour < 21:
        greeting = "Good evening"
    else:
        greeting = "Good evening"   # 21:00–04:59 — late evening/night

    return (
        f"[This is an automated briefing trigger — generate their proactive briefing now.] "
        f"Start your message with '{greeting}, {{first_name}}' where {{{{first_name}}}} is replaced "
        f"with the user's actual name from their profile. "
        "Then give a concise market briefing. If they have followed sectors/watchlist, personalize: "
        "what's moved, notable news, anything worth their attention. "
        "If no watchlist/sectors saved yet, give a brief general market snapshot "
        "(major indices, 1-2 significant headlines) — never send nothing. "
        "Use your tools for real current data. Keep it short and scannable."
    )

# In-memory store — prevents duplicate rate alerts within a calendar day
# Format: "gemini_50_2026-08-15" → True
_RATE_ALERT_SENT: dict[str, bool] = {}

# Approximate daily request limits — adjust to match your actual plan
_GEMINI_DAILY_LIMIT = 1500   # gemini-2.0-flash free tier (~1500 req/day)
_GROQ_DAILY_LIMIT   = 14400  # groq free tier (~10 RPM * 60 * 24)


# ── Shared helpers ─────────────────────────────────────────────────────────────

async def _push_message(bot, chat_id: int | str, text: str):
    """Send with Markdown, fall back to plain text."""
    formatted = _to_telegram_markdown(text)
    try:
        await bot.send_message(chat_id=int(chat_id), text=formatted, parse_mode="Markdown")
    except Exception:
        try:
            await bot.send_message(chat_id=int(chat_id), text=text)
        except Exception as exc:
            print(f"[Scheduler] Push failed for {chat_id}: {exc}")


def _is_allowed_recipient(db, telegram_id: str) -> bool:
    """Only push to users who are on the allowlist (or are owner)."""
    if telegram_id in OWNER_TELEGRAM_IDS:
        return True
    return db.query(AllowedUser).filter(AllowedUser.telegram_id == telegram_id).first() is not None


# ── Job 1: Daily briefings ─────────────────────────────────────────────────────

async def _send_briefing(bot, user_id: int):
    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .join(AllowedUser, AllowedUser.telegram_id == User.telegram_id)
            .filter(User.id == user_id)
            .first()
        )
        if not user:
            return

        profile_summary = build_profile_summary(user)
        try:
            reply_text = get_reply(db, user.telegram_id, [], _get_briefing_prompt(), profile_summary)
        except Exception as exc:
            print(f"[Briefing] Generation error for {user.telegram_id}: {exc}")
            return

        await _push_message(bot, user.telegram_id, reply_text)
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
        candidates = [u for u in candidates if _is_allowed_recipient(db, u.telegram_id)]
        user_ids = [u.id for u in candidates]
    finally:
        db.close()

    for user_id in user_ids:
        await _send_briefing(bot, user_id)


# ── Job 2 + 3: Alert checker + RSI re-armer ──────────────────────────────────

async def _check_and_send_alerts(bot):
    """Checks all active alerts and pushes notifications for triggered ones."""
    from app.services.alert_engine import check_active_alerts, re_arm_rsi_alerts

    db = SessionLocal()
    try:
        def _send_fn(telegram_id: str, text: str):
            """
            Sync wrapper — the alert engine is sync; scheduler is async.
            We collect messages and send after the sync check completes.
            """
            _pending_alert_messages.append((telegram_id, text))

        _pending_alert_messages.clear()
        check_active_alerts(db, _send_fn)
        re_arm_rsi_alerts(db)
    finally:
        db.close()

    # Now actually send the messages (async)
    for telegram_id, text in _pending_alert_messages:
        db2 = SessionLocal()
        try:
            if _is_allowed_recipient(db2, telegram_id):
                await _push_message(bot, telegram_id, text)
        finally:
            db2.close()


_pending_alert_messages: list[tuple[str, str]] = []


# ── Job 4: Daily baseline reset for recurring index PERCENT alerts ─────────────

async def _reset_index_baselines(bot):
    """
    Runs at 9:16 AM IST on weekdays, just after NSE market open.
    Resets baseline_price on all recurring PERCENT_DROP/GAIN index alerts
    so "alert if Nifty drops 0.5% today" always uses today's open price.
    """
    from app.services.alert_engine import reset_daily_baselines
    db = SessionLocal()
    try:
        count = reset_daily_baselines(db)
        print(f"[Scheduler] Baseline reset: {count} alerts updated.")
    except Exception as exc:
        print(f"[Scheduler] Baseline reset error: {exc}")
    finally:
        db.close()


# ── Job 5: API rate limit monitor — owner-only alerts ─────────────────────────

async def _check_api_rate_limits(bot):
    """
    Estimates today's LLM usage from the messages DB table.
    Sends Telegram alert to OWNER_TELEGRAM_IDS at 50%, 90%, and 100% of daily limit.
    Uses assistant message count as a proxy for API calls (1 assistant reply ≈ 1 call).
    """
    from app.database.db import Message

    db = SessionLocal()
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    try:
        today_start = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = (
            db.query(Message)
            .filter(
                Message.role == "assistant",
                Message.created_at >= today_start,
            )
            .count()
        )
    finally:
        db.close()

    for api_name, limit in [("Gemini", _GEMINI_DAILY_LIMIT), ("Groq", _GROQ_DAILY_LIMIT)]:
        pct = (today_count / limit) * 100

        thresholds = [
            (100, "🔴 QUOTA EXHAUSTED", True),
            (90,  "🟠 90% used",        False),
            (50,  "🟡 50% used",        False),
        ]

        for threshold, label, is_critical in thresholds:
            key = f"{api_name.lower()}_{threshold}_{today_str}"
            if pct >= threshold and key not in _RATE_ALERT_SENT:
                _RATE_ALERT_SENT[key] = True

                msg = (
                    f"⚠️ *Atlas API Usage Alert*\n\n"
                    f"API: *{api_name}*\n"
                    f"Status: *{label}*\n"
                    f"Est. calls today: *{today_count}* / {limit}\n"
                    f"Usage: *{pct:.0f}%*\n\n"
                )
                if is_critical:
                    msg += "🚨 Bot may stop responding until midnight IST. Consider switching to fallback."
                else:
                    msg += "Monitor usage — high traffic detected."

                for owner_id in OWNER_TELEGRAM_IDS:
                    await _push_message(bot, owner_id, msg)

                break  # Only send the highest threshold hit, don't stack alerts


# ── Scheduler startup ──────────────────────────────────────────────────────────

def start_scheduler(bot):
    scheduler = AsyncIOScheduler(timezone=IST)

    # Job 1: Briefings — every minute
    scheduler.add_job(
        _check_and_send_briefings, "interval",
        minutes=1, args=[bot]
    )

    # Job 2+3: Alert checker — every 15 min
    scheduler.add_job(
        _check_and_send_alerts, "interval",
        minutes=15, args=[bot]
    )

    # Job 4: Daily baseline reset — 9:16 AM IST, Mon–Fri only
    scheduler.add_job(
        _reset_index_baselines, "cron",
        day_of_week="mon-fri", hour=9, minute=16,
        args=[bot], timezone=IST
    )

    # Job 5: API rate monitor — every 60 min
    scheduler.add_job(
        _check_api_rate_limits, "interval",
        minutes=60, args=[bot]
    )

    scheduler.start()
    print("[Scheduler] Started: briefings (1 min), alerts (15 min), baseline reset (9:16 AM IST), rate monitor (60 min).")
    return scheduler