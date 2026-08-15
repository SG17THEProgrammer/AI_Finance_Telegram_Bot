from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from app.database.db import SessionLocal, get_or_create_user

# ---------------------------------------------------------------------------
# STATE DEFINITIONS
# Flow: intent -> experience -> horizon -> goal -> markets -> risk_q1 ->
#       risk_q2 -> risk_q3 -> watchlist -> done
# ---------------------------------------------------------------------------
(
    ASK_INTENT,
    ASK_EXPERIENCE,
    ASK_HORIZON,
    ASK_GOAL,
    WAIT_CUSTOM_GOAL,
    ASK_MARKETS,
    ASK_RISK_Q1,
    ASK_RISK_Q2,
    ASK_RISK_Q3,
    ASK_WATCHLIST,
) = range(10)

# ---------------------------------------------------------------------------
# Risk scoring: each answer maps to a score.
# Total score -> Conservative / Moderate / Aggressive
# ---------------------------------------------------------------------------
RISK_SCORES = {
    # Q1 — portfolio drop scenario
    "rq1_a": 1,  # sell everything
    "rq1_b": 2,  # sell some
    "rq1_c": 3,  # hold
    "rq1_d": 4,  # buy more
    # Q2 — priority statement
    "rq2_a": 1,  # protect money
    "rq2_b": 2,  # moderate growth
    "rq2_c": 3,  # balanced
    "rq2_d": 4,  # maximum growth
    # Q3 — experience with volatility
    "rq3_a": 1,  # very uncomfortable
    "rq3_b": 2,  # somewhat uncomfortable
    "rq3_c": 3,  # somewhat comfortable
    "rq3_d": 4,  # very comfortable
}


def _score_to_profile(score: int) -> str:
    if score <= 5:
        return "Conservative"
    elif score <= 9:
        return "Moderate"
    else:
        return "Aggressive"


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _make_keyboard(buttons: list) -> InlineKeyboardMarkup:
    """Each item in buttons is (label, callback_data). One button per row."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)]
                                 for label, data in buttons])


async def _reply(update: Update, text: str, keyboard=None):
    """Send or edit depending on whether this came from a callback or message."""
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )


# ---------------------------------------------------------------------------
# STEP 1 — INTENT
# ---------------------------------------------------------------------------

async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ── ACCESS CONTROL GATE ────────────────────────────────────────────────────
    from app.database.db import SessionLocal
    from app.bot.access_control import is_allowed, record_allowed_user

    db = SessionLocal()
    try:
        if not is_allowed(update, db):
            await update.effective_message.reply_text(
                "🔒 This is an invite-only bot.\n\n"
                "You haven't been granted access yet. "
                "Please contact the admin to get added."
            )
            return ConversationHandler.END  # exits the conversation immediately
        record_allowed_user(db, update)
    finally:
        db.close()
    # ── END ACCESS CONTROL ─────────────────────────────────────────────────────
    
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name

    db = SessionLocal()
    try:
        get_or_create_user(db, telegram_id, first_name)
    finally:
        db.close()

    # Reset any partial onboarding state
    context.user_data.clear()

    text = (
        f"👋 Welcome to Atlas AI, {first_name}!\n\n"
        "I'm your financial research assistant. Let's personalise Atlas for you "
        "— takes about 60 seconds.\n\n"
        "*What are you mainly using Atlas for?*"
    )
    keyboard = _make_keyboard([
        ("📈 Long-term investing", "intent_longterm"),
        ("⚡ Short-term / swing trading", "intent_shortterm"),
        ("🔎 Stock research", "intent_research"),
        ("📰 Market & news analysis", "intent_news"),
        ("💰 Portfolio analysis", "intent_portfolio"),
        ("📚 Learn investing", "intent_learn"),
        ("✍️ Other (type below)", "intent_other"),
    ])
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_INTENT


async def handle_intent_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "intent_other":
        await query.edit_message_text("Got it — type what you mainly want to use Atlas for:")
        return ASK_INTENT

    intent_map = {
        "intent_longterm":  "Long-term investing",
        "intent_shortterm": "Short-term / swing trading",
        "intent_research":  "Stock research",
        "intent_news":      "Market & news analysis",
        "intent_portfolio": "Portfolio analysis",
        "intent_learn":     "Learning to invest",
    }
    context.user_data["intent"] = intent_map[query.data]
    return await ask_experience(update, context)


async def handle_free_text_intent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["intent"] = update.message.text.strip()
    return await ask_experience(update, context, is_message=True)


# ---------------------------------------------------------------------------
# STEP 2 — EXPERIENCE
# ---------------------------------------------------------------------------

async def ask_experience(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    text = "Got it. *How experienced are you with investing?*"
    keyboard = _make_keyboard([
        ("🌱 Beginner — just getting started", "exp_beginner"),
        ("📊 Intermediate — a few years in", "exp_intermediate"),
        ("🧠 Advanced — experienced investor", "exp_advanced"),
    ])
    if is_message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_EXPERIENCE


async def handle_experience_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["experience_level"] = query.data.split("_")[
        1].capitalize()

    text = "*What's your typical investment time horizon?*"
    keyboard = _make_keyboard([
        ("< 1 year", "horizon_lt1"),
        ("1–3 years", "horizon_1to3"),
        ("3–5 years", "horizon_3to5"),
        ("5+ years", "horizon_5plus"),
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_HORIZON


# ---------------------------------------------------------------------------
# STEP 3 — HORIZON
# ---------------------------------------------------------------------------

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

    text = "*What is your primary investment goal?*"
    keyboard = _make_keyboard([
        ("🏦 Wealth creation", "goal_wealth"),
        ("📈 Capital appreciation", "goal_capital"),
        ("💰 Regular income", "goal_income"),
        ("🏠 Major future purchase (Car / House)", "goal_purchase"),
        ("🎓 Retirement", "goal_retirement"),
        ("✍️ Other (type below)", "goal_other"),
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_GOAL


# ---------------------------------------------------------------------------
# STEP 4 — GOAL
# ---------------------------------------------------------------------------

async def handle_goal_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "goal_other":
        await query.edit_message_text("What's your main financial goal? (Type it out)")
        return WAIT_CUSTOM_GOAL

    goal_map = {
        "goal_wealth":    "Wealth creation",
        "goal_capital":   "Capital appreciation",
        "goal_income":    "Regular income",
        "goal_purchase":  "Major future purchase",
        "goal_retirement": "Retirement",
    }
    context.user_data["primary_goal"] = goal_map[query.data]
    return await ask_markets(update, context)


async def handle_custom_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["primary_goal"] = update.message.text.strip()
    return await ask_markets(update, context, is_message=True)


# ---------------------------------------------------------------------------
# STEP 5 — PREFERRED MARKETS
# ---------------------------------------------------------------------------

# async def ask_markets(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
#     text = "*Which markets do you follow?*\n\n_Pick your primary market (you can tell me about others later)._"
#     keyboard = _make_keyboard([
#         ("🇮🇳 Indian Stocks (NSE / BSE)", "market_india"),
#         ("🇺🇸 US Stocks (NYSE / NASDAQ)", "market_us"),
#         ("🪙 Crypto", "market_crypto"),
#         ("📊 ETFs", "market_etf"),
#         ("🏦 Mutual Funds", "market_mf"),
#         ("🌎 Global / Multiple", "market_global"),
#     ])
#     if is_message:
#         await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
#     else:
#         await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
#     return ASK_MARKETS

# ── Market multi-select state ──────────────────────────────────────────────────

MARKET_OPTIONS = [
    ("🇮🇳 Indian Stocks", "indian_stocks"),
    ("🇺🇸 US Stocks", "us_stocks"),
    ("🪙 Crypto", "crypto"),
    ("📊 ETFs", "etfs"),
    ("🏦 Mutual Funds", "mutual_funds"),
    ("🌎 Global / Multiple", "global"),
]


def _build_market_keyboard(selected: set) -> InlineKeyboardMarkup:
    """Builds the markets keyboard with ✅ on already-selected options."""
    buttons = []
    for label, code in MARKET_OPTIONS:
        prefix = "✅ " if code in selected else ""
        buttons.append([InlineKeyboardButton(
            f"{prefix}{label}", callback_data=f"mkt_{code}")])
    buttons.append([InlineKeyboardButton(
        "✓ Done — continue", callback_data="mkt_done")])
    return InlineKeyboardMarkup(buttons)


async def ask_markets(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    if "selected_markets" not in context.user_data:
        context.user_data["selected_markets"] = set()

    text = "Which markets do you follow? *(select all that apply, then tap Done)*"
    keyboard = _build_market_keyboard(context.user_data["selected_markets"])

    if is_message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_MARKETS


async def handle_market_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggles a market option on/off without advancing the state."""
    query = update.callback_query
    await query.answer()

    code = query.data[4:]  # strip "mkt_"

    if code == "done":
        selected = context.user_data.get("selected_markets", set())
        context.user_data["preferred_markets"] = ",".join(
            selected) if selected else "not specified"
        return await ask_risk(update, context)

    selected = context.user_data.get("selected_markets", set())
    if code in selected:
        selected.discard(code)
    else:
        selected.add(code)
    context.user_data["selected_markets"] = selected

    # Re-render same message with updated checkmarks
    keyboard = _build_market_keyboard(selected)
    await query.edit_message_reply_markup(reply_markup=keyboard)
    return ASK_MARKETS  # stay in same state


# ---------------------------------------------------------------------------
# STEP 6 — RISK PROFILING (3 scenario questions, no hints on answers)
# ---------------------------------------------------------------------------

async def ask_risk_q1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Almost done — a few quick questions to understand how you think about risk.\n\n"
        "*Question 1 of 3*\n\n"
        "Your ₹1,00,000 portfolio drops to ₹75,000 during a market crash. "
        "What do you do?"
    )
    keyboard = _make_keyboard([
        ("Sell everything immediately", "rq1_a"),
        ("Sell some to reduce exposure", "rq1_b"),
        ("Hold and wait it out", "rq1_c"),
        ("Buy more while prices are low", "rq1_d"),
    ])
    await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_RISK_Q1


async def handle_risk_q1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["risk_score"] = RISK_SCORES[query.data]

    text = (
        "*Question 2 of 3*\n\n"
        "Which statement best describes your investing mindset?"
    )
    keyboard = _make_keyboard([
        ("I prioritise protecting what I have", "rq2_a"),
        ("I want modest growth with limited risk", "rq2_b"),
        ("I'm okay with ups and downs for better returns", "rq2_c"),
        ("I want maximum growth, whatever it takes", "rq2_d"),
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_RISK_Q2


async def handle_risk_q2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["risk_score"] += RISK_SCORES[query.data]

    text = (
        "*Question 3 of 3*\n\n"
        "How do you feel when an investment you hold drops 20% in a month?"
    )
    keyboard = _make_keyboard([
        ("Very stressed — I'd want out immediately", "rq3_a"),
        ("Uncomfortable, but I'd wait a little", "rq3_b"),
        ("Okay — I'd review but probably stay", "rq3_c"),
        ("Fine — short-term drops don't bother me", "rq3_d"),
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ASK_RISK_Q3


async def handle_risk_q3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["risk_score"] += RISK_SCORES[query.data]

    risk_profile = _score_to_profile(context.user_data["risk_score"])
    context.user_data["risk_profile"] = risk_profile

    text = (
        "Last step — *let's build your watchlist.*\n\n"
        "Type up to 5 stocks you currently follow "
        "(e.g. `RELIANCE TCS HDFCBANK`), or send `skip` to do this later."
    )
    await query.edit_message_text(text, parse_mode="Markdown")
    return ASK_WATCHLIST


# ---------------------------------------------------------------------------
# STEP 7 — WATCHLIST + FINALIZE
# ---------------------------------------------------------------------------

# async def handle_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     raw = update.message.text.strip()
#     if raw.lower() != "skip":
#         tickers = [t.strip().upper() for t in raw.replace(",", " ").split() if t.strip()]
#         context.user_data["watchlist"] = ", ".join(tickers[:5])

#     telegram_id = str(update.effective_user.id)
#     db = SessionLocal()
#     try:
#         user = get_or_create_user(db, telegram_id)
#         user.intent = context.user_data.get("intent")
#         user.experience_level = context.user_data.get("experience_level")
#         user.investment_horizon = context.user_data.get("investment_horizon")
#         user.primary_goal = context.user_data.get("primary_goal")
#         user.preferred_markets = context.user_data.get("preferred_markets")
#         user.risk_profile = context.user_data.get("risk_profile")
#         if context.user_data.get("watchlist"):
#             user.watchlist = context.user_data["watchlist"]
#         user.onboarded = 1
#         db.commit()
#     finally:
#         db.close()

#     risk = context.user_data.get("risk_profile", "")
#     risk_desc = {
#         "Conservative": "You prefer stability and capital preservation.",
#         "Moderate":     "You balance growth with manageable risk.",
#         "Aggressive":   "You're comfortable with volatility in pursuit of high returns.",
#     }.get(risk, "")

#     watchlist_line = (
#         f"👀 *Watchlist:* {context.user_data.get('watchlist')}\n"
#         if context.user_data.get("watchlist") else ""
#     )

#     summary = (
#         "✅ *Your Atlas Profile is Ready!*\n\n"
#         f"🧭 *Intent:* {context.user_data.get('intent')}\n"
#         f"📊 *Experience:* {context.user_data.get('experience_level')}\n"
#         f"⏳ *Horizon:* {context.user_data.get('investment_horizon')}\n"
#         f"🎯 *Goal:* {context.user_data.get('primary_goal')}\n"
#         f"🌍 *Markets:* {context.user_data.get('preferred_markets')}\n"
#         f"⚖️ *Risk Profile:* {risk} — _{risk_desc}_\n"
#         f"{watchlist_line}\n"
#         "Type a question or send a stock ticker like `RELIANCE` to get started. "
#         "Use /profile anytime to see this again."
#     )
#     await update.message.reply_text(summary, parse_mode="Markdown")
#     return ConversationHandler.END

# Curated suggestions — 8 high-interest stocks across sectors
_WATCHLIST_SUGGESTIONS = [
    ("RELIANCE", "Energy/Conglomerate"),
    ("TCS", "IT"),
    ("HDFCBANK", "Banking"),
    ("INFY", "IT"),
    ("TATAMOTORS", "Auto/EV"),
    ("SUNPHARMA", "Pharma"),
    ("AAPL", "US Tech"),
    ("NVDA", "US AI/Chips"),
]


async def ask_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    if "watchlist_picks" not in context.user_data:
        context.user_data["watchlist_picks"] = []

    picks = context.user_data.get("watchlist_picks", [])
    picked_tickers = [p[0] for p in picks]

    buttons = []
    row = []
    for ticker, sector in _WATCHLIST_SUGGESTIONS:
        prefix = "✅ " if ticker in picked_tickers else ""
        row.append(InlineKeyboardButton(
            f"{prefix}{ticker}", callback_data=f"wl_{ticker}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Action row
    buttons.append([
        InlineKeyboardButton("✓ Done with selections",
                             callback_data="wl_done"),
        InlineKeyboardButton("⏭ Skip", callback_data="wl_skip"),
    ])

    count_str = f" ({len(picks)}/5 selected)" if picks else ""
    text = (
        f"📈 *Build your watchlist*{count_str}\n\n"
        "Tap stocks below to add them, *or just type ticker names* "
        "(e.g. `WIPRO ZOMATO` or `Apple Microsoft`). "
        "Mix and match — tap + type is fine.\n\n"
        "_Trending picks across sectors:_"
    )

    if is_message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    else:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return ASK_WATCHLIST


async def handle_watchlist_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    code = query.data[3:]  # strip "wl_"

    if code == "skip":
        return await finalize_onboarding(update, context, watchlist=[])

    if code == "done":
        picks = context.user_data.get("watchlist_picks", [])
        return await finalize_onboarding(update, context, watchlist=[t for t, _ in picks])

    # Toggle the tapped stock
    picks = context.user_data.get("watchlist_picks", [])
    tickers = [t for t, _ in picks]

    sector = next((s for t, s in _WATCHLIST_SUGGESTIONS if t == code), "")
    if code in tickers:
        picks = [(t, s) for t, s in picks if t != code]
    elif len(picks) < 5:
        picks.append((code, sector))
    # else: already at 5, silently ignore (could add a toast)

    context.user_data["watchlist_picks"] = picks
    # Re-render
    return await ask_watchlist(update, context)  # re-calls edit_message_text


async def handle_watchlist_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Smart ticker extraction: calls a lightweight LLM prompt to convert
    natural language stock mentions into clean NSE/NYSE tickers.
    Falls back to whitespace split if LLM call fails.
    """
    raw = update.message.text.strip()
    extracted = await _extract_tickers_from_text(raw)

    picks = context.user_data.get("watchlist_picks", [])
    existing = [t for t, _ in picks]
    added = []

    for ticker in extracted:
        if ticker not in existing and len(picks) < 5:
            picks.append((ticker, ""))
            added.append(ticker)

    context.user_data["watchlist_picks"] = picks

    if added:
        await update.message.reply_text(
            f"✅ Added: *{', '.join(added)}*\n"
            f"Watchlist so far: *{', '.join([t for t, _ in picks])}*\n\n"
            "Tap more from the buttons, type more names, or tap *Done* to continue.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "I couldn't identify any stock tickers in that. Try ticker symbols like "
            "`TCS`, `RELIANCE`, `AAPL` — or tap the suggestions above.",
            parse_mode="Markdown"
        )

    return await ask_watchlist(update, context, is_message=True)


async def _extract_tickers_from_text(raw: str) -> list[str]:
    """
    Uses Gemini with a strict short prompt to extract stock tickers.
    Falls back to simple uppercase word split if anything fails.
    """
    import asyncio
    from app.config import GEMINI_API_KEY, GEMINI_MODEL
    from google import genai
    from google.genai import types as gtypes

    prompt = (
        f"Extract only the stock ticker symbols from this text: '{raw}'\n"
        "Rules:\n"
        "- Return ONLY a comma-separated list of uppercase ticker symbols. Nothing else.\n"
        "- Convert company names to their NSE/NYSE ticker (Apple→AAPL, Reliance→RELIANCE, "
        "  Berkshire Hathaway→BRK-B, Infosys→INFY, etc.)\n"
        "- Ignore non-stock words like 'value', 'investing', 'growth', 'sector', etc.\n"
        "- If nothing maps to a real stock, return: NONE\n"
        "Examples:\n"
        "  'apple berkshire hathaway value investing' → AAPL,BRK-B\n"
        "  'wipro zomato' → WIPRO,ZOMATO\n"
        "  'growth stocks only' → NONE\n"
        f"Input: '{raw}'\nOutput:"
    )

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=prompt,
            config=gtypes.GenerateContentConfig(
                temperature=0.0, max_output_tokens=60
            )
        )
        result = response.text.strip().upper()
        if result == "NONE" or not result:
            return []
        tickers = [t.strip() for t in result.split(
            ",") if t.strip() and len(t.strip()) <= 12]
        return tickers[:5]
    except Exception:
        # Fallback: split on spaces/commas and filter to plausible tickers
        import re
        words = re.split(r"[\s,]+", raw.upper())
        return [w for w in words if 2 <= len(w) <= 8 and w.isalpha()][:5]


# ---------------------------------------------------------------------------
# /profile COMMAND
# ---------------------------------------------------------------------------

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = get_or_create_user(
            db, telegram_id, update.effective_user.first_name)
        if not user.onboarded:
            await update.effective_message.reply_text(
                "You haven't completed onboarding yet — send /start to set up your profile."
            )
            return
        text = (
            "🧠 *Your Atlas Profile*\n\n"
            f"🧭 *Intent:* {user.intent or 'Not set'}\n"
            f"📊 *Experience:* {user.experience_level or 'Not set'}\n"
            f"⏳ *Horizon:* {user.investment_horizon or 'Not set'}\n"
            f"🎯 *Goal:* {user.primary_goal or 'Not set'}\n"
            f"🌍 *Markets:* {user.preferred_markets or 'Not set'}\n"
            f"⚖️ *Risk Profile:* {user.risk_profile or 'Not set'}\n"
            f"👀 *Watchlist:* {user.watchlist or 'Not set'}\n\n"
            "Send /start to redo onboarding and update these anytime."
        )
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CONVERSATION HANDLER
# ---------------------------------------------------------------------------

onboarding_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_onboarding)],
    states={
        ASK_INTENT: [
            CallbackQueryHandler(handle_intent_button, pattern="^intent_"),
            MessageHandler(filters.TEXT & ~filters.COMMAND,
                           handle_free_text_intent),
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
            MessageHandler(filters.TEXT & ~filters.COMMAND,
                           handle_custom_goal),
        ],
        ASK_MARKETS: [CallbackQueryHandler(handle_market_toggle, pattern='^mkt_')],
        ASK_RISK_Q1: [
            CallbackQueryHandler(handle_risk_q1, pattern="^rq1_"),
        ],
        ASK_RISK_Q2: [
            CallbackQueryHandler(handle_risk_q2, pattern="^rq2_"),
        ],
        ASK_RISK_Q3: [
            CallbackQueryHandler(handle_risk_q3, pattern="^rq3_"),
        ],
        ASK_WATCHLIST: [
            CallbackQueryHandler(handle_watchlist_button, pattern='^wl_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND,
                           handle_watchlist_text),
        ],
    },
    fallbacks=[CommandHandler("start", start_onboarding)],
)
