"""
Telegram only allows ONE active connection mode at a time - once Railway sets
a webhook, local `python run_polling.py` will fail with a conflict error.

Run this before switching back to local polling for development:
    python clear_webhook.py

You do NOT need to run this before deploying - main.py sets the webhook
automatically on startup.
"""
import asyncio
from telegram import Bot

from app.config import TELEGRAM_BOT_TOKEN


async def main():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.delete_webhook(drop_pending_updates=False)
    print("Webhook cleared - local polling (run_polling.py) will work again now.")


if __name__ == "__main__":
    asyncio.run(main())