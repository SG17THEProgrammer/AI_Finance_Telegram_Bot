"""
Run this for local testing. It connects to Telegram via polling (no public URL,
no ngrok, no deployment needed) - perfect for the adversarial testing you've
been doing. When you're ready to deploy for real (Railway/Render), we'll
switch to the webhook mode in main.py instead.

Usage:
    python run_polling.py
"""
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.config import TELEGRAM_BOT_TOKEN
from app.db import init_db
from app.handlers import start_command, handle_text, handle_voice, handle_photo, handle_document


def main():
    init_db()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))

    print("Atlas is running (polling mode). Go message your bot on Telegram now.")
    app.run_polling()


if __name__ == "__main__":
    main()