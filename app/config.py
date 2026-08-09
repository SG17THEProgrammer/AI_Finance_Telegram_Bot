import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Primary chat model - Gemini. Note: Google has tightened free-tier eligibility
# in 2026 - some fresh API keys get a hard "limit: 0" on free tier regardless of
# usage. Groq below is kept as an automatic fallback for exactly this scenario,
# not just for voice - this mirrors the reference bot's config, which requires
# both keys for the same reason.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Fallback chat model if Gemini is unavailable, plus used for voice transcription later
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./atlas.db")
PUBLIC_WEBHOOK_URL = os.getenv("PUBLIC_WEBHOOK_URL", "")

if not TELEGRAM_BOT_TOKEN:
    print("[WARN] TELEGRAM_BOT_TOKEN is not set. Fill it in your .env file.")
if not GEMINI_API_KEY:
    print("[WARN] GEMINI_API_KEY is not set. Will rely entirely on Groq fallback.")
if not GROQ_API_KEY:
    print("[WARN] GROQ_API_KEY is not set. No fallback available if Gemini fails.")
if not FINNHUB_API_KEY:
    print("[WARN] FINNHUB_API_KEY is not set. US stock quotes/news will be limited. Fill it in your .env file.")