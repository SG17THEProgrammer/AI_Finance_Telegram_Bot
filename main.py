"""
Production entry point using FastAPI + Telegram webhook, per the chosen architecture.
Use run_polling.py for local testing during development instead - it's simpler and
needs no public URL. Switch to this once deploying to Railway/Render.

Usage (after deployment, with PUBLIC_WEBHOOK_URL set in .env):
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager

from app.bot.onboarding import onboarding_handler, profile_command
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

# special Python class from the python-telegram-bot library. This class takes that giant, messy JSON payload and converts it into a clean, easy-to-use Python object.
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.config import TELEGRAM_BOT_TOKEN, PUBLIC_WEBHOOK_URL
from app.database.db import init_db, SessionLocal, get_or_create_user
from app.bot.handlers import handle_text, handle_voice, handle_photo, handle_document, allow_command, remove_command, allowed_command, id_command, myalerts_command
from app.integrations.google_oauth import exchange_code_for_tokens
from app.scheduler.scheduler import start_scheduler

# This takes your secret Telegram Token and creates the bot object.
telegram_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

telegram_app.add_handler(onboarding_handler)
telegram_app.add_handler(CommandHandler("profile", profile_command))
telegram_app.add_handler(CommandHandler("myalerts", myalerts_command))
telegram_app.add_handler(CommandHandler("allow", allow_command))
telegram_app.add_handler(CommandHandler("remove", remove_command))
telegram_app.add_handler(CommandHandler("allowed", allowed_command))


telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
telegram_app.add_handler(MessageHandler(filters.VOICE, handle_voice))
telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
telegram_app.add_handler(MessageHandler(filters.Document.PDF, handle_document))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db() # initializes the SQLite/Postgres database tables
    await telegram_app.initialize()
    await telegram_app.start()

    # Telling Telegram exactly where to send new messages
    if PUBLIC_WEBHOOK_URL:
        await telegram_app.bot.set_webhook(url=f"{PUBLIC_WEBHOOK_URL}/webhook")

    # turns on the background timer for daily briefings
    scheduler = start_scheduler(telegram_app.bot)

    yield # for Startup

    scheduler.shutdown()
    await telegram_app.stop()
    await telegram_app.shutdown()


#This creates the API that runs on the internet
app = FastAPI(lifespan=lifespan) 


@app.post("/webhook")
async def telegram_webhook(request: Request):
    # It grabs the raw data from Telegram and translates it into a Python Update object.
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    # It throws that update into the bot, which then triggers the correct handler.
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def health():
    return {"status": "Atlas is alive"}


@app.get("/oauth2callback")
async def oauth2callback(request: Request):
    # google sends code and state
    code = request.query_params.get("code")
    telegram_id = request.query_params.get("state")
    error = request.query_params.get("error")

    if error or not code or not telegram_id:
        return HTMLResponse(
            "<h2>Connection failed or was cancelled.</h2><p>Go back to Telegram and try again.</p>",
            status_code=400,
        )

    try:
        # We trade Google's temporary code for a permanent refresh_token
        tokens = exchange_code_for_tokens(code)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            # Happens if the user had already granted consent before and Google
            # didn't re-issue a refresh_token - prompt=consent in build_auth_url
            # is meant to prevent this, but handle it defensively anyway.
            return HTMLResponse(
                "<h2>Almost there</h2><p>Please revoke Atlas's access in your "
                "<a href='https://myaccount.google.com/permissions' target='_blank'>Google account permissions</a> "
                "and try connecting again from Telegram.</p>",
                status_code=400,
            )

        # We open the database, find the user by their Telegram ID, save their refresh_token, and commit the change. Now the bot can read their sheets!
        db = SessionLocal()
        try:
            user = get_or_create_user(db, telegram_id)
            user.google_refresh_token = refresh_token
            db.commit()
        finally:
            db.close()

        return HTMLResponse(
            "<h2>Connected! ✅</h2><p>You can close this tab and go back to Telegram.</p>"
        )
    except Exception as exc:
        print(f"[OAuth callback error] {type(exc).__name__}: {exc}")
        return HTMLResponse("<h2>Something went wrong connecting your account.</h2>", status_code=500)