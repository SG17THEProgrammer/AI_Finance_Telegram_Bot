"""
app/bot/onboarding.py

Button-driven onboarding flow for Atlas.

Fixes in this version:
  1. Access control gate at /start — unauthorized users blocked immediately
  2. Multi-select market keyboard with toggle (✅) pattern + Done button
  3. Suggested watchlist stocks (tap to add) + smart LLM ticker extraction
     from free text so "apple berkshire hathaway" → ["AAPL", "BRK-B"]
  4. finalize_onboarding() properly defined and saves all 7 fields
  5. /profile command shows current profile
"""

import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)

from app.database.db import SessionLocal, get_or_create_user

# ── State machine constants ────────────────────────────────────────────────────
(
    ASK_INTENT,
    WAIT_CUSTOM_INTENT,
    ASK_EXPERIENCE,
    ASK_HORIZON,
    ASK_GOAL,
    WAIT_CUSTOM_GOAL,
    ASK_MARKETS,
    ASK_RISK_Q1,
    ASK_RISK_Q2,
    ASK_RISK_Q3,
    ASK_WATCHLIST,
) = range(11)

# ── Risk scoring ───────────────────────────────────────────────────────────────
# Each question answer maps to a score (1=low risk, 4=high risk)
# Total 3–5 → Conservative, 6–9 → Moderate, 10–12 → Aggressive
_RISK_SCORES = {
    "r1_sell":    1, "r1_reduce":  2, "r1_hold":   3, "r1_buy":    4,
    "r2_protect": 1, "r2_balance": 2, "r2_growth":  3, "r2_max":    4,
    "r3_cash":    1, "r3_bonds":   2, "r3_mixed":   3, "r3_equities": 4,
}

def _score_to_profile(score: int) -> str:
    if score <= 5:
        return "Conservative"
    elif score <= 9:
        return "Moderate"
    return "Aggressive"

# ── Market options for multi-select ───────────────────────────────────────────
_MARKET_OPTIONS = [
    ("🇮🇳 Indian Stocks",  "indian_stocks"),
    ("🇺🇸 US Stocks",      "us_stocks"),
    ("🪙 Crypto",           "crypto"),
    ("📊 ETFs",             "etfs"),
    ("🏦 Mutual Funds",    "mutual_funds"),
    ("🌎 Global / Multiple","global"),
]

# ── Curated watchlist suggestions ─────────────────────────────────────────────
_WATCHLIST_SUGGESTIONS = [
    ("RELIANCE",  "Energy"),
    ("TCS",       "IT"),
    ("HDFCBANK",  "Banking"),
    ("INFY",      "IT"),
    ("TATAMOTORS","Auto/EV"),
    ("SUNPHARMA", "Pharma"),
    ("AAPL",      "US Tech"),
    ("NVDA",      "US AI"),
]


# ── Entry point: /start ────────────────────────────────────────────────────────

async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Entry point. Checks access control FIRST — unauthorized users get
    blocked before seeing any onboarding content.
    """
    # ── ACCESS CONTROL GATE ──────────────────────────────────────────────────
    from app.bot.access_control import is_allowed, record_allowed_user
    db = SessionLocal()
    try:
        if not is_allowed(update, db):
            await update.effective_message.reply_text(
                "🔒 *Access Denied*\n\n"
                "This is an invite-only bot. Contact the admin to request access.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        record_allowed_user(db, update)
    finally:
        db.close()
    # ── END ACCESS CONTROL ───────────────────────────────────────────────────

    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name or "there"

    db = SessionLocal()
    try:
        get_or_create_user(db, telegram_id, first_name)
    finally:
        db.close()

    # Reset any leftover state from a previous incomplete onboarding
    context.user_data.clear()

    welcome_text = (
        f"👋 Welcome to *Atlas AI*, {first_name}!\n\n"
        "I'm your AI financial research assistant. Let's personalise Atlas for you — "
        "takes about 60 seconds.\n\n"
        "*What are you mainly using Atlas for?*"
    )
    keyboard = [
        [InlineKeyboardButton("📈 Long-term investing",       callback_data="intent_longterm")],
        [InlineKeyboardButton("⚡ Short-term / swing",         callback_data="intent_shortterm")],
        [InlineKeyboardButton("🔎 Stock research",             callback_data="intent_research")],
        [InlineKeyboardButton("📰 Market & news analysis",    callback_data="intent_news")],
        [InlineKeyboardButton("💰 Portfolio analysis",        callback_data="intent_portfolio")],
        [InlineKeyboardButton("📚 Learn investing",           callback_data="intent_learn")],
        [InlineKeyboardButton("✍️ Other — type it",           callback_data="intent_other")],
    ]

    msg = update.message or (update.callback_query and update.callback_query.message)
    if msg:
        await msg.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_INTENT


# ── Intent handlers ────────────────────────────────────────────────────────────

async def handle_intent_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "intent_other":
        await query.edit_message_text("Sure! Type what you mainly want to use Atlas for:")
        return WAIT_CUSTOM_INTENT

    intent_map = {
        "intent_longterm":  "Long-term investing",
        "intent_shortterm": "Short-term / swing trading",
        "intent_research":  "Stock research",
        "intent_news":      "Market & news analysis",
        "intent_portfolio": "Portfolio analysis",
        "intent_learn":     "Learning to invest",
    }
    context.user_data["intent"] = intent_map.get(query.data, "General")
    return await _ask_experience(update, context)


async def handle_custom_intent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["intent"] = update.message.text.strip()
    return await _ask_experience(update, context, is_message=True)


# ── Experience ─────────────────────────────────────────────────────────────────

async def _ask_experience(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    text = "Got it! *How experienced are you with investing?*"
    keyboard = [[
        InlineKeyboardButton("🌱 Beginner",      callback_data="exp_beginner"),
        InlineKeyboardButton("📊 Intermediate",  callback_data="exp_intermediate"),
        InlineKeyboardButton("🧠 Advanced",      callback_data="exp_advanced"),
    ]]
    if is_message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_EXPERIENCE


async def handle_experience_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["experience_level"] = query.data.split("_")[1].capitalize()
    return await _ask_horizon(update, context)


# ── Investment horizon ─────────────────────────────────────────────────────────

async def _ask_horizon(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    text = "What is your typical *investment time horizon?*"
    keyboard = [
        [InlineKeyboardButton("⚡ Less than 1 year",  callback_data="horizon_lt1")],
        [InlineKeyboardButton("📅 1 – 3 years",       callback_data="horizon_1to3")],
        [InlineKeyboardButton("📆 3 – 5 years",       callback_data="horizon_3to5")],
        [InlineKeyboardButton("🏔️ 5+ years",          callback_data="horizon_5plus")],
    ]
    if is_message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_HORIZON


async def handle_horizon_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    horizon_map = {
        "horizon_lt1":   "< 1 year",
        "horizon_1to3":  "1–3 years",
        "horizon_3to5":  "3–5 years",
        "horizon_5plus": "5+ years",
    }
    context.user_data["investment_horizon"] = horizon_map[query.data]
    return await _ask_goal(update, context)


# ── Investment goal ────────────────────────────────────────────────────────────

async def _ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    text = "What is your *primary investment goal?*"
    keyboard = [
        [InlineKeyboardButton("🏦 Wealth creation",           callback_data="goal_wealth")],
        [InlineKeyboardButton("📈 Capital appreciation",      callback_data="goal_capital")],
        [InlineKeyboardButton("💵 Regular income",            callback_data="goal_income")],
        [InlineKeyboardButton("🏠 Major future purchase",     callback_data="goal_purchase")],
        [InlineKeyboardButton("🎓 Retirement",                callback_data="goal_retirement")],
        [InlineKeyboardButton("✍️ Other — type it",           callback_data="goal_other")],
    ]
    if is_message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_GOAL


async def handle_goal_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "goal_other":
        await query.edit_message_text("Type your main financial goal (e.g. 'Save for a wedding'):")
        return WAIT_CUSTOM_GOAL
    goal_map = {
        "goal_wealth":     "Wealth creation",
        "goal_capital":    "Capital appreciation",
        "goal_income":     "Regular income",
        "goal_purchase":   "Major future purchase",
        "goal_retirement": "Retirement",
    }
    context.user_data["primary_goal"] = goal_map[query.data]
    return await _ask_markets(update, context)


async def handle_custom_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["primary_goal"] = update.message.text.strip()
    return await _ask_markets(update, context, is_message=True)


# ── Markets — multi-select with toggle ────────────────────────────────────────

def _build_market_keyboard(selected: set) -> InlineKeyboardMarkup:
    """Builds the markets keyboard. ✅ prefix on selected options."""
    buttons = []
    for label, code in _MARKET_OPTIONS:
        prefix = "✅ " if code in selected else ""
        buttons.append([InlineKeyboardButton(f"{prefix}{label}", callback_data=f"mkt_{code}")])
    buttons.append([InlineKeyboardButton("✓ Done — continue", callback_data="mkt_done")])
    return InlineKeyboardMarkup(buttons)


async def _ask_markets(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    if "selected_markets" not in context.user_data:
        context.user_data["selected_markets"] = set()
    text = "Which *markets do you follow?*\n_(Select all that apply, then tap Done)_"
    keyboard = _build_market_keyboard(context.user_data["selected_markets"])
    if is_message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_MARKETS


async def handle_market_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles market selection without advancing state. 'Done' advances."""
    query = update.callback_query
    await query.answer()

    code = query.data[4:]  # strip "mkt_"

    if code == "done":
        selected = context.user_data.get("selected_markets", set())
        context.user_data["preferred_markets"] = ",".join(selected) if selected else "not_specified"
        return await _ask_risk_q1(update, context)

    selected = context.user_data.get("selected_markets", set())
    if code in selected:
        selected.discard(code)
    else:
        selected.add(code)
    context.user_data["selected_markets"] = selected

    # Re-render same message with updated checkmarks
    keyboard = _build_market_keyboard(selected)
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception:
        pass  # Message unchanged is fine
    return ASK_MARKETS


# ── Risk profiling — 3 scenario questions ─────────────────────────────────────

async def _ask_risk_q1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Almost done! A few quick scenarios to gauge your risk appetite.\n\n"
        "*Your ₹1,00,000 portfolio falls to ₹75,000 during a market crash. You:*"
    )
    keyboard = [
        [InlineKeyboardButton("😰 Sell most of it",        callback_data="r1_sell")],
        [InlineKeyboardButton("📉 Reduce exposure a bit",  callback_data="r1_reduce")],
        [InlineKeyboardButton("😐 Hold and wait it out",   callback_data="r1_hold")],
        [InlineKeyboardButton("💪 Buy more — great dip",   callback_data="r1_buy")],
    ]
    try:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception:
        await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_RISK_Q1


async def handle_risk_q1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.setdefault("risk_score", 0)
    context.user_data["risk_score"] += _RISK_SCORES.get(query.data, 2)

    text = "*Which statement describes you best?*"
    keyboard = [
        [InlineKeyboardButton("🛡️ I prioritise protecting my money",          callback_data="r2_protect")],
        [InlineKeyboardButton("⚖️ I accept moderate swings for some growth",  callback_data="r2_balance")],
        [InlineKeyboardButton("📈 I aim for growth, okay with volatility",    callback_data="r2_growth")],
        [InlineKeyboardButton("🚀 I want maximum returns, even if volatile",  callback_data="r2_max")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_RISK_Q2


async def handle_risk_q2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["risk_score"] += _RISK_SCORES.get(query.data, 2)

    text = "*If you had ₹1 lakh to invest right now, where would you put it?*"
    keyboard = [
        [InlineKeyboardButton("🏦 Fixed deposits / cash",            callback_data="r3_cash")],
        [InlineKeyboardButton("📜 Government bonds / debt funds",    callback_data="r3_bonds")],
        [InlineKeyboardButton("🔀 Mix of equity + debt",             callback_data="r3_mixed")],
        [InlineKeyboardButton("📊 Mostly equities / stocks",         callback_data="r3_equities")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_RISK_Q3


async def handle_risk_q3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["risk_score"] += _RISK_SCORES.get(query.data, 2)

    total = context.user_data["risk_score"]
    context.user_data["risk_profile"] = _score_to_profile(total)

    return await _ask_watchlist(update, context)


# ── Watchlist — suggested picks + free text ───────────────────────────────────

def _build_watchlist_keyboard(picks: list) -> InlineKeyboardMarkup:
    picked_tickers = [t for t, _ in picks]
    buttons = []
    row = []
    for ticker, sector in _WATCHLIST_SUGGESTIONS:
        prefix = "✅ " if ticker in picked_tickers else ""
        row.append(InlineKeyboardButton(f"{prefix}{ticker}", callback_data=f"wl_{ticker}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton("✓ Done",  callback_data="wl_done"),
        InlineKeyboardButton("⏭ Skip",  callback_data="wl_skip"),
    ])
    return InlineKeyboardMarkup(buttons)


async def _ask_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    if "watchlist_picks" not in context.user_data:
        context.user_data["watchlist_picks"] = []

    picks = context.user_data["watchlist_picks"]
    count_str = f" *({len(picks)}/5 selected)*" if picks else ""

    text = (
        f"📈 *Build your watchlist*{count_str}\n\n"
        "Tap stocks to add them, *or just type names/tickers* "
        "(e.g. `Wipro Zomato` or `Apple Microsoft Berkshire`). "
        "Tap + type works too.\n\n"
        "_Trending picks across sectors:_"
    )

    keyboard = _build_watchlist_keyboard(picks)

    if is_message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_WATCHLIST


async def handle_watchlist_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    code = query.data[3:]  # strip "wl_"

    if code == "skip":
        return await finalize_onboarding(update, context, watchlist_tickers=[])

    if code == "done":
        picks = context.user_data.get("watchlist_picks", [])
        return await finalize_onboarding(update, context, watchlist_tickers=[t for t, _ in picks])

    # Toggle stock on/off
    picks = context.user_data.get("watchlist_picks", [])
    tickers = [t for t, _ in picks]
    sector = next((s for t, s in _WATCHLIST_SUGGESTIONS if t == code), "")

    if code in tickers:
        picks = [(t, s) for t, s in picks if t != code]
    elif len(picks) < 5:
        picks.append((code, sector))

    context.user_data["watchlist_picks"] = picks
    return await _ask_watchlist(update, context)  # re-render with updated ticks


async def handle_watchlist_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Smart ticker extraction: passes user's free text through a lightweight
    LLM call to extract real stock tickers.
    Handles: "apple berkshire hathaway" → ["AAPL", "BRK-B"]
             "wipro zomato"             → ["WIPRO", "ZOMATO"]
             "growth stocks only"       → [] (nothing actionable)
    """
    raw = update.message.text.strip()
    extracted = await _extract_tickers_from_text(raw)

    picks = context.user_data.get("watchlist_picks", [])
    existing_tickers = [t for t, _ in picks]
    added = []

    for ticker in extracted:
        if ticker not in existing_tickers and len(picks) < 5:
            picks.append((ticker, ""))
            added.append(ticker)

    context.user_data["watchlist_picks"] = picks

    if added:
        await update.message.reply_text(
            f"✅ Added: *{', '.join(added)}*\n"
            f"Watchlist so far: *{', '.join([t for t, _ in picks])}*\n\n"
            "Tap more suggestions, type more names, or tap *Done* to finish.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "I couldn't identify any stock tickers in that. "
            "Try ticker symbols like `TCS`, `RELIANCE`, `AAPL`, "
            "or company names like `Apple`, `Infosys`.",
            parse_mode="Markdown"
        )

    return await _ask_watchlist(update, context, is_message=True)


async def _extract_tickers_from_text(raw: str) -> list:
    """
    Uses Gemini with a zero-temperature prompt to extract ticker symbols
    from natural language. Falls back to simple alpha-word split if it fails.
    """
    from app.config import GEMINI_API_KEY, GEMINI_MODEL
    try:
        from google import genai
        from google.genai import types as gtypes

        prompt = (
            f"Extract only stock ticker symbols from this text: '{raw}'\n"
            "Rules:\n"
            "- Return ONLY a comma-separated list of uppercase tickers. Nothing else.\n"
            "- Convert company names to their NSE/NYSE ticker "
            "  (Apple→AAPL, Reliance→RELIANCE, Berkshire Hathaway→BRK-B, "
            "  Infosys→INFY, Wipro→WIPRO, Zomato→ZOMATO, etc.)\n"
            "- Ignore non-stock words: 'value', 'investing', 'growth', 'sector', etc.\n"
            "- If nothing maps to a real stock, return: NONE\n"
            "Examples:\n"
            "  'apple berkshire hathaway value investing' → AAPL,BRK-B\n"
            "  'wipro zomato' → WIPRO,ZOMATO\n"
            "  'growth stocks only' → NONE\n"
            f"Input: '{raw}'\nOutput:"
        )

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=prompt,
            config=gtypes.GenerateContentConfig(temperature=0.0, max_output_tokens=60),
        )
        result = (response.text or "").strip().upper()
        if result == "NONE" or not result:
            return []
        return [t.strip() for t in result.split(",") if t.strip() and len(t.strip()) <= 12][:5]

    except Exception:
        # Fallback: split on spaces/commas, keep plausible ticker-shaped words
        import re
        words = re.split(r"[\s,]+", raw.upper())
        return [w for w in words if 2 <= len(w) <= 8 and w.isalpha()][:5]


# ── Finalize onboarding — saves everything to DB ──────────────────────────────

async def finalize_onboarding(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    watchlist_tickers: list
):
    """
    Saves all collected onboarding data to the User row in the DB.
    Sends a summary card and exits the ConversationHandler.
    """
    telegram_id = str(update.effective_user.id)
    ud = context.user_data

    db = SessionLocal()
    try:
        user = get_or_create_user(db, telegram_id)
        user.intent             = ud.get("intent")
        user.experience_level   = ud.get("experience_level")
        user.investment_horizon = ud.get("investment_horizon")
        user.primary_goal       = ud.get("primary_goal")
        user.preferred_markets  = ud.get("preferred_markets", "not_specified")
        user.risk_profile       = ud.get("risk_profile", "Moderate")
        user.watchlist          = ",".join(watchlist_tickers) if watchlist_tickers else None
        user.onboarded          = 1
        db.commit()
    finally:
        db.close()

    # Human-readable market labels
    market_code_to_label = {code: label for label, code in _MARKET_OPTIONS}
    markets_str = ud.get("preferred_markets", "")
    markets_display = (
        ", ".join(market_code_to_label.get(c, c) for c in markets_str.split(",") if c)
        if markets_str and markets_str != "not_specified"
        else "Not specified"
    )

    watchlist_str = ", ".join(watchlist_tickers) if watchlist_tickers else "None added yet"

    risk = ud.get("risk_profile", "Moderate")
    risk_desc = {
        "Conservative": "Capital preservation — favours stable, lower-risk assets.",
        "Moderate":     "Balanced growth — comfortable with some market swings.",
        "Aggressive":   "High growth — accepts significant volatility for returns.",
    }.get(risk, "")

    summary = (
        "✅ *Your Atlas Profile is Ready!*\n\n"
        f"🎯 *Goal:* {ud.get('primary_goal', 'Not set')}\n"
        f"📊 *Experience:* {ud.get('experience_level', 'Not set')}\n"
        f"⏳ *Horizon:* {ud.get('investment_horizon', 'Not set')}\n"
        f"🌍 *Markets:* {markets_display}\n"
        f"⚖️ *Risk Profile:* {risk} — _{risk_desc}_\n"
        f"📈 *Watchlist:* {watchlist_str}\n\n"
        "You can update any of this anytime with `/profile`.\n"
        "Now ask me anything — or type a ticker like `TCS` or `AAPL` to start! 🚀"
    )

    try:
        await update.callback_query.edit_message_text(summary, parse_mode="Markdown")
    except Exception:
        try:
            await update.callback_query.message.reply_text(summary, parse_mode="Markdown")
        except Exception:
            await update.effective_message.reply_text(summary, parse_mode="Markdown")

    return ConversationHandler.END


# ── /profile command ───────────────────────────────────────────────────────────

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the user's current saved profile."""
    from app.bot.access_control import is_allowed
    db = SessionLocal()
    try:
        if not is_allowed(update, db):
            await update.message.reply_text("🔒 Access denied.")
            return

        telegram_id = str(update.effective_user.id)
        user = get_or_create_user(db, telegram_id)

        if not user.onboarded:
            await update.message.reply_text(
                "You haven't completed onboarding yet. Use /start to set up your profile.",
                parse_mode="Markdown"
            )
            return

        market_code_to_label = {code: label for label, code in _MARKET_OPTIONS}
        markets_str = user.preferred_markets or ""
        markets_display = (
            ", ".join(market_code_to_label.get(c, c) for c in markets_str.split(",") if c)
            if markets_str and markets_str != "not_specified"
            else "Not specified"
        )

        text = (
            "🧠 *Your Atlas Profile*\n\n"
            f"🎯 *Goal:* {user.primary_goal or 'Not set'}\n"
            f"📊 *Experience:* {user.experience_level or 'Not set'}\n"
            f"⏳ *Horizon:* {user.investment_horizon or 'Not set'}\n"
            f"🌍 *Markets:* {markets_display}\n"
            f"⚖️ *Risk Profile:* {user.risk_profile or 'Not set'}\n"
            f"📈 *Watchlist:* {user.watchlist or 'None'}\n"
            f"🕐 *Daily briefing:* {user.briefing_time or 'Not set'}\n\n"
            "Use /start to update your profile anytime."
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    finally:
        db.close()


# ── ConversationHandler export ─────────────────────────────────────────────────

onboarding_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_onboarding)],
    states={
        ASK_INTENT: [
            CallbackQueryHandler(handle_intent_button, pattern="^intent_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_intent),
        ],
        WAIT_CUSTOM_INTENT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_intent),
        ],
        ASK_EXPERIENCE: [
            CallbackQueryHandler(handle_experience_button, pattern="^exp_"),
        ],
        ASK_HORIZON: [
            CallbackQueryHandler(handle_horizon_button, pattern="^horizon_"),
        ],
        ASK_GOAL: [
            CallbackQueryHandler(handle_goal_button, pattern="^goal_"),
        ],
        WAIT_CUSTOM_GOAL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_goal),
        ],
        ASK_MARKETS: [
            CallbackQueryHandler(handle_market_toggle, pattern="^mkt_"),
        ],
        ASK_RISK_Q1: [
            CallbackQueryHandler(handle_risk_q1, pattern="^r1_"),
        ],
        ASK_RISK_Q2: [
            CallbackQueryHandler(handle_risk_q2, pattern="^r2_"),
        ],
        ASK_RISK_Q3: [
            CallbackQueryHandler(handle_risk_q3, pattern="^r3_"),
        ],
        ASK_WATCHLIST: [
            CallbackQueryHandler(handle_watchlist_button, pattern="^wl_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_watchlist_text),
        ],
    },
    fallbacks=[CommandHandler("start", start_onboarding)],
    allow_reentry=True,
)