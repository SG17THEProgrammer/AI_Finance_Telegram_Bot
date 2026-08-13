from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from app.database.db import SessionLocal, get_or_create_user  # Note: adjust import paths if needed based on your setup

# --- STATE DEFINITIONS ---
# NOTE: order here matches the actual conversation order below:
# intent -> experience -> horizon -> goal -> risk -> watchlist -> done
(
    ASK_INTENT,
    ASK_EXPERIENCE,
    ASK_HORIZON,
    ASK_GOAL,
    WAIT_CUSTOM_GOAL,
    ASK_RISK,
    ASK_WATCHLIST,
) = range(7)

async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered by /start or if the user clicks a 'Personalize' button."""
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name

    # Initialize user in DB
    db = SessionLocal()
    try:
        get_or_create_user(db, telegram_id, first_name)
    finally:
        db.close()

    welcome_text = (
        f"👋 Welcome to Atlas AI, {first_name}!\n\n"
        "I'm your financial research assistant. Before we dive into the markets, "
        "let's personalize Atlas for you. It takes just 60 seconds.\n\n"
        "**What are you mainly using Atlas for?**"
    )

    keyboard = [
        [InlineKeyboardButton("📈 Long-term investing", callback_data="intent_longterm")],
        [InlineKeyboardButton("⚡ Short-term / swing", callback_data="intent_shortterm")],
        [InlineKeyboardButton("🔎 Stock research", callback_data="intent_research")],
        [InlineKeyboardButton("✍️ Other (Type yourself)", callback_data="intent_other")]
    ]
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.callback_query.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    return ASK_INTENT


async def handle_intent_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "intent_other":
        await query.edit_message_text("Got it! Please type out what you mainly want to use Atlas for:")
        return ASK_INTENT  # stay in the same state - the free-text fallback below will catch the reply

    # Save standard intent
    intent_map = {
        "intent_longterm": "Long-term investing",
        "intent_shortterm": "Short-term / swing",
        "intent_research": "Stock research"
    }
    context.user_data['intent'] = intent_map[query.data]
    return await ask_experience(update, context)


async def handle_free_text_intent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lets the user just type their intent directly at any point in this
    step, instead of requiring them to tap 'Other' first."""
    context.user_data['intent'] = update.message.text.strip()
    return await ask_experience(update, context, is_message=True)


async def ask_experience(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    text = "Great! Next, **how experienced are you with investing?**"
    keyboard = [
        [
            InlineKeyboardButton("🌱 Beginner", callback_data="exp_beginner"),
            InlineKeyboardButton("📊 Intermediate", callback_data="exp_intermediate"),
            InlineKeyboardButton("🧠 Advanced", callback_data="exp_advanced")
        ]
    ]
    
    if is_message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_EXPERIENCE


async def handle_experience_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['experience_level'] = query.data.split("_")[1].capitalize()

    text = "Got it. **What's your typical investment time horizon?**"
    keyboard = [
        [InlineKeyboardButton("< 1 year", callback_data="horizon_lt1")],
        [InlineKeyboardButton("1–3 years", callback_data="horizon_1to3")],
        [InlineKeyboardButton("3–5 years", callback_data="horizon_3to5")],
        [InlineKeyboardButton("5+ years", callback_data="horizon_5plus")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_HORIZON


async def handle_horizon_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    horizon_map = {
        "horizon_lt1": "< 1 year",
        "horizon_1to3": "1–3 years",
        "horizon_3to5": "3–5 years",
        "horizon_5plus": "5+ years",
    }
    context.user_data['investment_horizon'] = horizon_map[query.data]

    text = "Got it. **What is your primary investment goal right now?**"
    keyboard = [
        [InlineKeyboardButton("🏦 Wealth creation", callback_data="goal_wealth")],
        [InlineKeyboardButton("🏠 Major purchase (Car/House)", callback_data="goal_purchase")],
        [InlineKeyboardButton("🎓 Retirement", callback_data="goal_retirement")],
        [InlineKeyboardButton("✍️ Other (Type yourself)", callback_data="goal_other")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_GOAL


async def handle_goal_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "goal_other":
        await query.edit_message_text("Please type out your main financial goal (e.g., 'Save for a wedding'):")
        return WAIT_CUSTOM_GOAL

    goal_map = {
        "goal_wealth": "Wealth creation",
        "goal_purchase": "Major future purchase",
        "goal_retirement": "Retirement"
    }
    context.user_data['primary_goal'] = goal_map[query.data]
    return await ask_risk(update, context)


async def handle_custom_goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['primary_goal'] = update.message.text
    return await ask_risk(update, context, is_message=True)


async def ask_risk(update: Update, context: ContextTypes.DEFAULT_TYPE, is_message=False):
    # SEBI Compliant Scenario-based Risk Profiling
    text = (
        "Almost done! Let's assess your risk appetite.\n\n"
        "**Imagine your ₹1,00,000 portfolio falls to ₹75,000 during a market crash. What would you do?**"
    )
    keyboard = [
        [InlineKeyboardButton("😰 Sell most of it (Low Risk)", callback_data="risk_conservative")],
        [InlineKeyboardButton("😐 Hold steady (Medium Risk)", callback_data="risk_moderate")],
        [InlineKeyboardButton("💪 Buy the dip (High Risk)", callback_data="risk_aggressive")]
    ]
    
    if is_message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ASK_RISK


async def handle_risk_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['risk_profile'] = query.data.split("_")[1].capitalize()

    text = (
        "Almost there! **Let's build your watchlist.**\n\n"
        "Type up to 5 stocks you currently follow (e.g. `RELIANCE TCS HDFCBANK`), "
        "or send `skip` to do this later."
    )
    await query.edit_message_text(text, parse_mode="Markdown")
    return ASK_WATCHLIST


async def handle_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    if raw.lower() != "skip":
        # Accept space or comma separated tickers, cap at 5, normalize to upper case.
        tickers = [t.strip().upper() for t in raw.replace(",", " ").split() if t.strip()]
        context.user_data['watchlist'] = ", ".join(tickers[:5])
    return await finalize_onboarding(update, context)


async def finalize_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Save everything to Database
    telegram_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = get_or_create_user(db, telegram_id)
        user.intent = context.user_data.get('intent')
        user.experience_level = context.user_data.get('experience_level')
        user.investment_horizon = context.user_data.get('investment_horizon')
        user.primary_goal = context.user_data.get('primary_goal')
        user.risk_profile = context.user_data.get('risk_profile')
        watchlist = context.user_data.get('watchlist')
        if watchlist:
            user.watchlist = watchlist
        user.onboarded = 1
        db.commit()
    finally:
        db.close()

    watchlist_line = (
        f"👀 **Watchlist:** {context.user_data.get('watchlist')}\n"
        if context.user_data.get('watchlist')
        else ""
    )

    summary_text = (
        "✅ **Your Atlas Profile is Ready!**\n\n"
        f"🧭 **Intent:** {context.user_data.get('intent')}\n"
        f"📊 **Experience:** {context.user_data.get('experience_level')}\n"
        f"⏳ **Horizon:** {context.user_data.get('investment_horizon')}\n"
        f"🎯 **Goal:** {context.user_data.get('primary_goal')}\n"
        f"⚖️ **Risk Profile:** {context.user_data.get('risk_profile')}\n"
        f"{watchlist_line}\n"
        "You can type your first question, use /profile anytime to see this again, "
        "or just send a stock ticker like `RELIANCE` or `TCS` to get started!"
    )
    await update.message.reply_text(summary_text, parse_mode="Markdown")
    return ConversationHandler.END


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/profile - shows the user's saved onboarding profile at any time."""
    telegram_id = str(update.effective_user.id)
    db = SessionLocal()
    try:
        user = get_or_create_user(db, telegram_id, update.effective_user.first_name)
        if not user.onboarded:
            await update.effective_message.reply_text(
                "You haven't completed onboarding yet - send /start to set up your profile."
            )
            return
        text = (
            "🧠 **Your Atlas Profile**\n\n"
            f"🧭 **Intent:** {user.intent or 'Not set'}\n"
            f"📊 **Experience:** {user.experience_level or 'Not set'}\n"
            f"⏳ **Horizon:** {user.investment_horizon or 'Not set'}\n"
            f"🎯 **Goal:** {user.primary_goal or 'Not set'}\n"
            f"⚖️ **Risk Profile:** {user.risk_profile or 'Not set'}\n"
            f"👀 **Watchlist:** {user.watchlist or 'Not set'}\n\n"
            "Send /start to redo onboarding and update these anytime."
        )
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    finally:
        db.close()


# --- THE CONVERSATION HANDLER SETUP ---
# You will import this `onboarding_handler` into your `main.py`
# Order matches the actual flow: intent -> experience -> horizon -> goal -> risk -> watchlist -> done
onboarding_handler = ConversationHandler(
    entry_points=[CommandHandler('start', start_onboarding)],
    states={
        ASK_INTENT: [
            CallbackQueryHandler(handle_intent_button, pattern='^intent_'),
            # Free typing works directly here too - no need to tap "Other" first.
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text_intent),
        ],

        ASK_EXPERIENCE: [CallbackQueryHandler(handle_experience_button, pattern='^exp_')],

        ASK_HORIZON: [CallbackQueryHandler(handle_horizon_button, pattern='^horizon_')],

        ASK_GOAL: [
            CallbackQueryHandler(handle_goal_button, pattern='^goal_'),
        ],
        WAIT_CUSTOM_GOAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_goal)],

        ASK_RISK: [CallbackQueryHandler(handle_risk_button, pattern='^risk_')],

        ASK_WATCHLIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_watchlist)],
    },
    fallbacks=[CommandHandler('start', start_onboarding)],
)