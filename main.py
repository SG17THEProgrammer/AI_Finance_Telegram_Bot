"""
Production entry point using FastAPI + Telegram webhook, per the chosen architecture.
Use run_polling.py for local testing during development instead - it's simpler and
needs no public URL. Switch to this once deploying to Railway/Render.

Usage (after deployment, with PUBLIC_WEBHOOK_URL set in .env):
    uvicorn main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.config import TELEGRAM_BOT_TOKEN, PUBLIC_WEBHOOK_URL
from app.db import init_db
from app.handlers import start_command, handle_text, handle_voice, handle_photo, handle_document

telegram_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
telegram_app.add_handler(CommandHandler("start", start_command))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
telegram_app.add_handler(MessageHandler(filters.VOICE, handle_voice))
telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
telegram_app.add_handler(MessageHandler(filters.Document.PDF, handle_document))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await telegram_app.initialize()
    await telegram_app.start()
    if PUBLIC_WEBHOOK_URL:
        await telegram_app.bot.set_webhook(url=f"{PUBLIC_WEBHOOK_URL}/webhook")
    yield
    await telegram_app.stop()
    await telegram_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.get("/")
async def health():
    return {"status": "Atlas is alive"}