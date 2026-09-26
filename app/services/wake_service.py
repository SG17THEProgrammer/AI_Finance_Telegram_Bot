"""
app/services/wake_service.py

Server wake-up service for Render deployments.

Render's free tier does NOT wake from external HTTP pings — the server
only wakes when Telegram forwards an inbound webhook update to it.

Strategy: at the start of each market window, send a silent bot message
to the owner's chat. Telegram forwards this to the webhook → Render wakes.
The message is tagged __WAKE__ and deleted immediately by the handler.

This works because:
1. Bot sends message → Telegram stores it
2. Telegram tries to deliver to webhook → Render cold-starts
3. Webhook receives update → handler sees __WAKE__ tag → deletes message silently
4. Server is now warm for the market window
"""

import httpx
from app.config import TELEGRAM_BOT_TOKEN, OWNER_TELEGRAM_IDS

WAKE_TAG = "ATLAS_WAKE"
_TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


async def send_wake_message() -> dict:
    """
    Sends a silent wake message to the first owner ID.
    Returns the message_id so it can be deleted after delivery.
    """
    if not OWNER_TELEGRAM_IDS:
        print("[WakeService] No OWNER_TELEGRAM_IDS configured — skipping wake.")
        return {}

    owner_id = next(iter(OWNER_TELEGRAM_IDS))

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": int(owner_id),
                    "text": WAKE_TAG,
                    "disable_notification": True,  # silent — no ping sound
                },
            )
            data = resp.json()
            if data.get("ok"):
                msg_id = data["result"]["message_id"]
                print(f"[WakeService] Wake message sent (id={msg_id}) to owner {owner_id}.")
                return {"chat_id": owner_id, "message_id": msg_id}
            else:
                print(f"[WakeService] Send failed: {data}")
                return {}
    except Exception as exc:
        print(f"[WakeService] Error sending wake message: {exc}")
        return {}


async def delete_wake_message(chat_id: str, message_id: int):
    """Deletes the wake message after the server has processed it."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{_TELEGRAM_API}/deleteMessage",
                json={"chat_id": int(chat_id), "message_id": message_id},
            )
    except Exception as exc:
        print(f"[WakeService] Delete error: {exc}")