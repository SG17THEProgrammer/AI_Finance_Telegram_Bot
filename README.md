<div align="center">

<img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
<img src="https://img.shields.io/badge/FastAPI-0.141-009688?style=for-the-badge&logo=fastapi&logoColor=white"/>
<img src="https://img.shields.io/badge/Telegram_Bot-22.8-26A5E4?style=for-the-badge&logo=telegram&logoColor=white"/>
<img src="https://img.shields.io/badge/Google_Gemini-2.0_Flash-4285F4?style=for-the-badge&logo=google&logoColor=white"/>
<img src="https://img.shields.io/badge/SQLite-SQLAlchemy-003B57?style=for-the-badge&logo=sqlite&logoColor=white"/>

<br/><br/>

# 🤖 Atlas AI — Financial Research Assistant

### A production-grade, invite-only Telegram bot that delivers real-time financial research, proactive market alerts, and dynamic chart generation — powered by Google Gemini with a Groq fallback.

<br/>

> *"Democratizing access to sophisticated financial intelligence directly inside Telegram."*

<br/>

[![Railway](https://img.shields.io/badge/Deployed_on-Railway-0B0D0E?style=flat-square&logo=railway)](https://railway.app)

</div>

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Live Features](#-live-features)
- [Architecture](#-architecture)
- [Folder Structure](#-folder-structure)
- [Tech Stack](#-tech-stack)
- [Onboarding Flow](#-onboarding-flow)
- [Alert System](#-alert-system)
- [Chart Engine](#-chart-engine)
- [SEBI Compliance Layer](#-sebi-compliance-layer)
- [Scheduler & Background Jobs](#-scheduler--background-jobs)
- [Database Schema](#-database-schema)
- [Installation](#-installation)
- [Environment Variables](#-environment-variables)
- [Running Locally](#-running-locally)
- [Deploying to Railway](#-deploying-to-railway)
- [Bot Commands](#-bot-commands)
- [Roadmap](#-roadmap)

---

## 🌐 Overview

Atlas is a **production-level, invite-only Telegram financial assistant** built for investors, analysts, and anyone who wants institutional-quality research delivered conversationally. It combines real-time market data, LLM-powered analysis, proactive threshold alerts, and dynamically generated charts — all without leaving Telegram.

Key design goals:
- **Zero hallucination** — every price, news item, and metric comes from a live tool call, never from model memory
- **SEBI-compliant** — risk guardrails are injected into every LLM call based on the user's onboarding profile
- **Proactive, not just reactive** — background scheduler checks alerts every 15 minutes and pushes notifications automatically
- **Invite-only access control** — no public access; owner grants access per user via `/allow`

---

## ✅ Live Features

### Phase 1 — Identity & Onboarding
- [x] 8-step button-driven onboarding: Intent → Experience → Horizon → Goal → Markets → Risk (3 questions) → Watchlist
- [x] Scenario-based SEBI-compliant risk profiling (Conservative / Moderate / Aggressive)
- [x] Multi-select market picker with toggle pattern (✅)
- [x] Smart watchlist setup: curated tap-suggestions + free-text ticker extraction via Gemini
- [x] `/profile` command to view saved profile anytime
- [x] Access control gate at `/start` — unauthorized users blocked immediately
- [x] Goal-based LLM framing injected per user (car purchase, retirement, wealth creation, etc.)

### Phase 2 — Alert Engine
- [x] 8 alert types: `PRICE_ABOVE`, `PRICE_BELOW`, `PERCENT_DROP`, `PERCENT_GAIN`, `RSI_OVERSOLD`, `RSI_OVERBOUGHT`, `TRAILING_DAYS`, `LAGGED_PERCENT_DROP`
- [x] Index support: Nifty 50, Bank Nifty, Sensex, Nasdaq 100, S&P 500
- [x] Recurring vs one-shot logic per alert type
- [x] Daily baseline reset at 9:16 AM IST for index percent alerts
- [x] RSI re-arming with 24-hour anti-flap cooldown
- [x] Automatic RSI chart attached when RSI alert fires
- [x] `/myalerts` command — deterministic, no LLM, shows all active alerts

### Phase 2 — Chart Engine (6 chart types)
- [x] Candlestick + MA50 & MA200 with Golden/Death Cross detection
- [x] RSI Gauge — two-panel (RSI line + price), overbought/oversold shading
- [x] Indian Sector Heatmap — gradient RdYlGn colormap with today vs yesterday comparison
- [x] US Sector Heatmap — SPDR ETF-based (XLK, XLV, XLF, etc.), dark theme tiles
- [x] Fundamental Radar Chart — P/E, P/B, ROE, Profit Margin, Revenue Growth vs market average
- [x] Support & Resistance Chart — auto-detected horizontal levels, deduplicated within 1.5% bands

### Core Capabilities
- [x] Live stock quotes — US (Finnhub) + Indian NSE/BSE (Yahoo Finance) with hybrid fallback
- [x] Company news — with `company_specific` flag (no fake attribution to loosely related headlines)
- [x] SEC EDGAR filings — US-listed companies only, honest scoping
- [x] Company fundamentals — P/E, market cap, sector, 52-week range
- [x] PDF document analysis — dual-grounding (visual + literal text extraction) for accuracy on financial tables
- [x] Voice message support — Groq Whisper transcription with confirmation step before LLM processing
- [x] Photo/chart screenshot analysis — Gemini multimodal
- [x] Google Sheets integration — OAuth2, read portfolio data directly from sheets
- [x] Safe arithmetic calculator — AST-based, never lets the LLM do mental math
- [x] Daily briefings — time-aware (Good morning/afternoon/evening), personalized to watchlist/sectors
- [x] Rate limiting — 25 messages/user/hour, sliding window, owners exempt
- [x] API usage monitor — owner notified at 50%, 90%, 100% of estimated daily Gemini/Groq quota
- [x] Auto schema migrations — `ALTER TABLE ADD COLUMN` on every deploy, zero downtime
- [x] Groq fallback — automatic if Gemini is unavailable or rate-limited

---

## 🏗️ Architecture

```
User (Telegram)
       │
       ▼
  Telegram API
       │  webhook (production) / polling (local)
       ▼
  FastAPI (main.py)
       │
       ├── /webhook  ─────────────────────────────────────► Telegram Update
       ├── /oauth2callback  ──────────────────────────────► Google OAuth flow
       └── /admin/restore-db (token-gated)  ─────────────► SQLite volume restore
       │
       ▼
  python-telegram-bot Application
       │
       ├── ConversationHandler (onboarding.py)
       ├── CommandHandlers (/profile, /myalerts, /allow, /remove, /allowed, /id)
       └── MessageHandlers (text, voice, photo, PDF)
       │
       ▼
  handlers.py  ──► llm.py  ──► Google Gemini 2.0 Flash
       │                  └──► Groq (fallback)
       │                         │
       │                         ▼ tool calls
       │                    tools.py → execute_tool_call()
       │                         │
       ├── financial_data.py ────┤ (quotes, news, fundamentals, SEC)
       ├── chart_engine.py ──────┤ (6 chart types → chart_{id}.png)
       ├── alert_engine.py ──────┤ (create/check/re-arm alerts)
       ├── calculator.py ────────┤ (safe AST arithmetic)
       ├── media.py ─────────────┤ (voice transcription, PDF text)
       └── google_sheets.py ─────┘ (read user spreadsheets)
       │
       ▼
  SQLite (atlas.db) via SQLAlchemy
       │
       ▼
  APScheduler (AsyncIOScheduler)
       ├── Every 1 min  → Daily briefings
       ├── Every 15 min → Alert checker + RSI re-armer (market hours only)
       ├── 9:16 AM IST  → Daily baseline reset (weekdays)
       └── Every 60 min → API rate monitor (owner-only alerts)
```

---

## 📁 Folder Structure

```
ai_finance_telegram_bot/
│
├── main.py                        # FastAPI entry point (webhook mode)
├── run_polling.py                 # Local development entry point
├── clear_webhook.py               # Utility: clears Telegram webhook for local dev
├── requirements.txt
├── Procfile                       # Railway/Heroku deployment config
│
└── app/
    ├── config.py                  # Env vars, API keys, owner IDs
    ├── system_prompt.py           # SYSTEM_PROMPT + get_system_prompt()
    │
    ├── bot/
    │   ├── onboarding.py          # 8-step ConversationHandler, /profile command
    │   ├── handlers.py            # Text/voice/photo/PDF handlers, build_profile_summary()
    │   └── access_control.py     # is_allowed(), /allow, /remove, allowlist
    │
    ├── database/
    │   └── db.py                  # SQLAlchemy models, auto-migrations, CRUD helpers
    │
    ├── services/
    │   ├── llm.py                 # Gemini + Groq paths, tool round-trip loop
    │   ├── tools.py               # TOOLS list + execute_tool_call() dispatcher
    │   ├── financial_data.py      # Quotes, news, fundamentals, basic charts
    │   ├── chart_engine.py        # 6 advanced chart types (dark theme)
    │   ├── alert_engine.py        # Alert creation, checking, re-arming, reset
    │   ├── calculator.py          # Safe AST arithmetic evaluator
    │   └── media.py               # Whisper voice transcription, PDF text extraction
    │
    ├── integrations/
    │   ├── google_oauth.py        # OAuth2 URL builder + token exchange
    │   └── google_sheets.py      # Sheet reader via Sheets API v4
    │
    └── scheduler/
        └── scheduler.py           # APScheduler: 4 background jobs
```

---

## 🛠️ Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **API Framework** | FastAPI 0.141 | Webhook endpoint, OAuth callback, admin restore |
| **Bot Library** | python-telegram-bot 22.8 | Telegram updates, inline keyboards, ConversationHandler |
| **Primary LLM** | Google Gemini 2.0 Flash | Chat, tool calling, multimodal (image + PDF) |
| **Fallback LLM** | Groq (openai/gpt-oss-20b) | Automatic fallback if Gemini fails or rate-limits |
| **Voice STT** | Groq Whisper large-v3-turbo | Voice message transcription |
| **Market Data (US)** | Finnhub API | Real-time US stock quotes, company news |
| **Market Data (India)** | yfinance (Yahoo Finance) | NSE/BSE quotes, historical data, fundamentals |
| **SEC Filings** | SEC EDGAR public API | 10-K, 10-Q, 8-K for US-listed companies |
| **Charts** | matplotlib + mplfinance | 6 dark-themed chart types, saved as PNG |
| **Database** | SQLite + SQLAlchemy 2.0 | Users, alerts, messages, auto-migrations |
| **Scheduler** | APScheduler 3.11 (AsyncIO) | 4 background jobs (briefings, alerts, resets, quota) |
| **Google Auth** | Google OAuth2 | Connect user's Google Sheets |
| **PDF Parsing** | pypdf + pypdfium2 | Dual-layer PDF reading (text + visual) |
| **Deployment** | Railway (via Procfile) | Persistent SQLite volume, auto-deploy |
| **Server** | Uvicorn | ASGI server for FastAPI |

---

## 🚀 Onboarding Flow

Atlas collects 7 data points in under 60 seconds using inline keyboard buttons. No typing required — except for the "Other" free-text option on any step.

```
/start
  │
  ▼
① What are you using Atlas for?
  [Long-term investing] [Short-term/swing] [Stock research]
  [Market & news] [Portfolio analysis] [Learn investing] [✍️ Other]
  │
  ▼
② How experienced are you?
  [🌱 Beginner] [📊 Intermediate] [🧠 Advanced]
  │
  ▼
③ What is your investment time horizon?
  [< 1 year] [1–3 years] [3–5 years] [5+ years]
  │
  ▼
④ What is your primary investment goal?
  [Wealth creation] [Capital appreciation] [Regular income]
  [Major future purchase] [Retirement] [✍️ Other]
  │
  ▼
⑤ Which markets do you follow? (multi-select with ✅ toggle)
  [🇮🇳 Indian Stocks] [🇺🇸 US Stocks] [🪙 Crypto]
  [📊 ETFs] [🏦 Mutual Funds] [🌎 Global]
  [✓ Done]
  │
  ▼
⑥ Risk Q1: "₹1,00,000 portfolio drops to ₹75,000. You:"
  [😰 Sell most] [📉 Reduce a bit] [😐 Hold] [💪 Buy more]
  │
⑥ Risk Q2: "Which statement describes you best?"
  [🛡️ Protect] [⚖️ Moderate swings] [📈 Growth] [🚀 Max returns]
  │
⑥ Risk Q3: "₹1 lakh to invest right now, where?"
  [🏦 Fixed deposits] [📜 Gov bonds] [🔀 Mix] [📊 Equities]
  │   → Score mapped: 3–5 = Conservative, 6–9 = Moderate, 10–12 = Aggressive
  │
  ▼
⑦ Build your watchlist (tap suggestions OR type company names)
  [RELIANCE] [TCS] [HDFCBANK] [INFY] [TATAMOTORS] [SUNPHARMA] [AAPL] [NVDA]
  [✓ Done] [⏭ Skip]
  │   → Free-text handled by Gemini zero-temp extraction ("apple berkshire" → AAPL, BRK-B)
  │
  ▼
✅ Profile Summary Card
  Goal | Experience | Horizon | Markets | Risk Profile | Watchlist
```

All 7 fields are saved to the `users` table. On every subsequent message, `build_profile_summary()` in `handlers.py` injects the full profile + SEBI guardrails into the LLM's system instruction.

---

## 🔔 Alert System

The alert engine (`app/services/alert_engine.py`) supports **8 alert types**:

| Alert Type | Behavior | Example |
|---|---|---|
| `PRICE_ABOVE` | One-shot. Fires when price crosses above target. | "Tell me when HDFC hits ₹1,800" |
| `PRICE_BELOW` | One-shot. Fires when price drops below target. | "Alert if TCS falls below ₹3,500" |
| `PERCENT_DROP` | Stock: one-shot from creation baseline. Index: recurring, baseline resets daily at 9:16 AM IST. | "Alert if Nifty drops 1% today" |
| `PERCENT_GAIN` | Same as above, for upside moves. | "Tell me if Reliance gains 2%" |
| `RSI_OVERSOLD` | Recurring. Fires when RSI ≤ threshold. Re-arms when RSI returns above 35. | "Alert when Infosys is oversold" |
| `RSI_OVERBOUGHT` | Recurring. Fires when RSI ≥ threshold. Re-arms when RSI drops below 65. | "Alert if Nifty is overbought" |
| `TRAILING_DAYS` | Recurring. Fires when ticker closes in the same direction for N of last M days. | "Alert if Nifty falls 5 of 8 days" |
| `LAGGED_PERCENT_DROP` | Recurring. Fires when ticker drops X% vs its price N trading days ago. | "Alert if Nifty drops 1.5% over 5 days" |

**Index aliases supported:** `NIFTY50`, `BANKNIFTY`, `SENSEX`, `NASDAQ100`, `SP500` (all mapped to yfinance symbols internally).

**Key behaviors:**
- Condition already met at creation time → returns `already_met: true`, no alert created, user told to set a different target
- `permanent=True` forces recurring on any alert type
- 24-hour anti-flap cooldown prevents spam on recurring alerts
- RSI alerts auto-attach the RSI chart when they fire

---

## 📊 Chart Engine

All charts are generated by `app/services/chart_engine.py`, saved as `chart_{telegram_id}.png`, picked up automatically by `handlers.py`, sent as a photo reply, then deleted from disk.

All charts share a consistent **dark theme** (`#0d1117` background, `#161b22` panels, white text forced throughout, DPI 150).

| Chart | Function | What it shows |
|---|---|---|
| **Candlestick + MA** | `generate_candlestick_with_ma()` | OHLCV candles + 50-day & 200-day moving averages. Detects Golden/Death Cross. Works for stocks and indices. |
| **RSI Gauge** | `generate_rsi_gauge()` | Two-panel: RSI-14 line with overbought (70) / oversold (30) shading + price line for context. |
| **Indian Sector Heatmap** | `generate_sector_heatmap()` | RdYlGn gradient grid of NSE sectors + global indices. Returns today + yesterday data for comparison queries. |
| **US Sector Heatmap** | `generate_us_sector_heatmap()` | SPDR ETF-based (XLK, XLV, XLF, etc.) dark-tile heatmap with legend gradient bar. |
| **Fundamental Radar** | `generate_fundamental_radar()` | Spider chart: P/E, P/B, ROE, Profit Margin, Revenue Growth vs approximate market averages. |
| **Support & Resistance** | `generate_support_resistance()` | Price line + auto-detected horizontal S/R levels (local extremes, deduplicated within 1.5% bands). |

The LLM is instructed in `system_prompt.py` exactly when to call each chart — it proactively generates charts without being explicitly asked (e.g. calling RSI chart automatically when an RSI alert fires).

---

## 🛡️ SEBI Compliance Layer

Atlas enforces regulatory-style suitability guardrails on every message based on the user's risk profile, injected by `build_profile_summary()` in `handlers.py`:

| Risk Profile | Guardrail Applied |
|---|---|
| **Conservative** | Must issue a warning *before* answering any question about Crypto, F&O/Options, or Micro-caps. Emphasizes capital preservation, debt instruments, blue-chip equities. |
| **Moderate** | Highlights downside risks alongside upside metrics. Balances growth with risk management. |
| **Aggressive** | Provides deeper technical/fundamental breakdowns. Reminds of stop-loss discipline and position sizing. |

Additionally, **goal-based framing** is injected per user:

- `< 1 year` horizon → prioritize liquidity, flag illiquid instruments
- `1–3 years` → large-cap equities, balanced funds, flag long lock-ups
- `3–5 years` → diversified equity, SIPs, highlight compounding
- `5+ years` → frame short-term swings as entry opportunities

Goal-specific framing covers: car/house purchase, retirement, regular income, wealth creation, capital appreciation, and freeform goals.

---

## ⏰ Scheduler & Background Jobs

`app/scheduler/scheduler.py` runs 4 jobs via `AsyncIOScheduler` (IST timezone):

| Job | Schedule | What it does |
|---|---|---|
| **Briefings** | Every 1 min | Checks `users.briefing_time` vs current IST time. If match and not already sent today, generates a personalized briefing (with tools). Falls back to general market snapshot if no watchlist/sectors. Greeting adapts to morning/afternoon/evening. |
| **Alert Checker** | Every 15 min, Mon–Fri, 9 AM–3 PM IST | Batch-fetches prices for all unique tickers in active alerts. Evaluates all 8 alert types. Pushes Telegram notification if triggered. Auto-attaches RSI chart for RSI alerts. |
| **RSI Re-Armer** | Same cycle as alert checker | Re-arms disarmed recurring alerts when condition exits zone. 24-hour cooldown enforced. |
| **Daily Baseline Reset** | 9:16 AM IST, Mon–Fri | Resets `baseline_price` on all recurring PERCENT_DROP/GAIN index alerts to previous day's close. Fixes the bug where Nifty % alerts were measured from creation time instead of today's open. |
| **API Rate Monitor** | Every 60 min | Estimates daily LLM usage from `messages` table (assistant role). Sends Telegram alert to `OWNER_TELEGRAM_IDS` at 50%, 90%, and 100% of estimated daily Gemini/Groq quota. |

---

## 🗄️ Database Schema

Managed by SQLAlchemy with automatic `ALTER TABLE ADD COLUMN` migrations on every startup (zero-downtime, never touches existing data).

### `users` table

| Column | Type | Description |
|---|---|---|
| `id` | Integer PK | |
| `telegram_id` | String (unique) | Telegram user ID |
| `first_name` | String | From Telegram |
| `role` | String | e.g. Investor, Analyst |
| `sectors` | Text | Comma-separated |
| `watchlist` | Text | Comma-separated tickers |
| `briefing_time` | String | HH:MM format |
| `language_pref` | String | |
| `onboarded` | Integer | 0 / 1 flag |
| `intent` | String | From onboarding step 1 |
| `experience_level` | String | Beginner / Intermediate / Advanced |
| `investment_horizon` | String | < 1 year / 1–3 years / 3–5 years / 5+ years |
| `risk_profile` | String | Conservative / Moderate / Aggressive |
| `primary_goal` | String | Wealth creation / Retirement / etc. |
| `preferred_markets` | String | Comma-separated market codes |
| `pending_transcript` | Text | Voice confirmation buffer |
| `google_refresh_token` | Text | Google OAuth refresh token |
| `last_briefing_date` | String | YYYY-MM-DD, prevents duplicate briefings |
| `created_at` | DateTime | UTC |

### `alerts` table

| Column | Type | Description |
|---|---|---|
| `id` | Integer PK | |
| `telegram_id` | String | Owner |
| `ticker` | String | yfinance-resolved symbol |
| `alert_type` | String | One of 8 types |
| `target_value` | String | Numeric threshold |
| `baseline_price` | String | Price at creation (for PERCENT types) |
| `baseline_date` | String | Date of last baseline reset |
| `is_recurring` | Integer | 0 = one-shot, 1 = recurring |
| `armed` | Integer | 1 = active, 0 = disarmed post-fire |
| `is_active` | Integer | 0 when soft-deleted |
| `triggered_at` | DateTime | Last fire time (for cooldown calc) |
| `extra_config` | Text | JSON blob for TRAILING_DAYS / LAGGED params |
| `created_at` | DateTime | UTC |

### `allowed_users` table

| Column | Type | Description |
|---|---|---|
| `id` | Integer PK | |
| `telegram_id` | String (unique) | May be `pending_<username>` until first message |
| `username` | String | @handle |
| `first_name` | String | |
| `is_owner` | Integer | 0 / 1 |
| `added_at` | DateTime | |

### `messages` table

| Column | Type | Description |
|---|---|---|
| `id` | Integer PK | |
| `telegram_id` | String | |
| `role` | String | `user` / `assistant` |
| `content` | Text | Message content |
| `expected_intent` | String | Unused, reserved |
| `created_at` | DateTime | UTC |

---

## ⚙️ Installation

### Prerequisites

- Python 3.10+
- pip
- A Telegram bot token (from [@BotFather](https://t.me/botfather))
- Google Gemini API key (from [Google AI Studio](https://aistudio.google.com/))
- Groq API key (from [console.groq.com](https://console.groq.com/))

### Clone and set up

```bash
git clone https://github.com/SG17THEProgrammer/AI_Finance_Telegram_Bot.git
cd AI_Finance_Telegram_Bot

python3 -m venv venv
source venv/bin/activate        # macOS/Linux
# .\venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

---

## 🔑 Environment Variables

Create a `.env` file in the project root:

```ini
# ── Core ──────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# ── LLM Providers ─────────────────────────────────────────────────────────────
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash

GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-20b

# ── Market Data ────────────────────────────────────────────────────────────────
FINNHUB_API_KEY=your_finnhub_api_key_here   # Free tier at finnhub.io

# ── Database ───────────────────────────────────────────────────────────────────
DATABASE_URL=sqlite:///./atlas.db           # Local
# DATABASE_URL=sqlite:////data/atlas.db    # Railway persistent volume

# ── Deployment ─────────────────────────────────────────────────────────────────
PUBLIC_WEBHOOK_URL=https://your-app.railway.app   # Leave blank for local polling

# ── Access Control ─────────────────────────────────────────────────────────────
OWNER_TELEGRAM_IDS=123456789,987654321
OWNER_TELEGRAM_USERNAMES=yourusername,anotheradmin

# ── Google OAuth (optional) ────────────────────────────────────────────────────
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REDIRECT_URI=https://your-app.railway.app/oauth2callback

# ── Admin (optional, leave blank to disable) ──────────────────────────────────
ADMIN_UPLOAD_TOKEN=your_secure_random_token_here
```

---

## 💻 Running Locally

```bash
# Step 1: If you previously deployed with a webhook, clear it first
python clear_webhook.py

# Step 2: Start the bot in polling mode
python run_polling.py
```

The bot will start polling Telegram. Open your bot in Telegram and send `/start`.

To inspect the database:
```bash
# Using SQLite CLI
sqlite3 atlas.db
sqlite> SELECT telegram_id, risk_profile, onboarded FROM users;
sqlite> SELECT ticker, alert_type, target_value, is_active FROM alerts;
```

---

## 🚀 Deploying to Railway

1. Push your code to GitHub
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add a **Volume** mounted at `/data`
4. Set `DATABASE_URL=sqlite:////data/atlas.db` in Railway env vars
5. Add all other env vars from the table above
6. Railway auto-deploys on every push — schema migrations run automatically on startup

**Procfile** (already included):
```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

To restore a local SQLite backup to your Railway volume (one-time):
```bash
curl -X POST "https://your-app.railway.app/admin/restore-db?token=YOUR_TOKEN" \
  -F "file=@atlas.db"
```

---

## 🤖 Bot Commands

| Command | Who | Description |
|---|---|---|
| `/start` | All allowed users | Run (or re-run) the onboarding flow |
| `/profile` | All allowed users | View your current Atlas profile |
| `/myalerts` | All allowed users | List all your active alerts |
| `/allow @username` | Owner only | Grant access to a new user |
| `/remove @username` | Owner only | Revoke a user's access |
| `/allowed` | Owner only | List all users currently allowed |
| `/id` | All | Show your Telegram numeric ID |

**Natural language commands the LLM handles directly:**
- *"Alert me if Nifty drops 1.5% today"* → creates `PERCENT_DROP` alert
- *"Show me a technical chart for TCS"* → generates candlestick + MA chart
- *"What sectors are up today?"* → generates Indian sector heatmap
- *"Is INFY oversold?"* → generates RSI gauge chart
- *"Analyze this annual report"* → processes uploaded PDF
- *"Cancel my TCS alert"* → calls `delete_market_alert` after listing

---

## 🗺️ Roadmap

### What's Complete
- ✅ Phase 1 — Onboarding & Identity Layer
- ✅ Phase 2 — Alert Engine (8 alert types)
- ✅ Phase 2 — Chart Engine (6 chart types)
- ✅ Infrastructure — Railway deployment, auto-migrations, rate limiting, API quota monitoring

### What's Next
- [ ] **Phase 3 — Goal-based investing logic**: LLM framing currently collected and stored. Next: derive concrete instrument recommendations from `primary_goal + investment_horizon` combination (e.g. SIP suggestions for car-purchase goal with 3-year horizon)
- [ ] **Phase 3 — US sector heatmap expansion**: Sector ETF drill-down with top holdings per sector
- [ ] **Phase 4 — Portfolio analysis**: CSV/PDF/screenshot upload → holding-level P&L, cost basis, unrealized gains
- [ ] **Phase 4 — Email alerts**: SMTP sender alongside Telegram push for critical alerts
- [ ] **Phase 4 — DZerv integration**: Broker-level portfolio sync


<div align="center">

Built with ☕ and a lot of market research.

*Atlas is a research and analysis tool. It is not a registered Investment Adviser. Nothing Atlas says constitutes financial advice. Always do your own research and consult a SEBI-registered advisor before making investment decisions.*

</div>
