# Atlas AI Financial Assistant — Phase 1

Phase 1 scope: conversation core only (no financial data, no integrations yet).
This is deliberately the part we stress-test hardest before building anything else,
per the plan.

## What's included in this phase
- `/start` → scripted welcome message (no LLM call)
- Free-text conversation via Groq (Llama 3.3 70B)
- Full system prompt with all guardrails discussed:
  - identity/scope lock (can't be jailbroken into a general assistant)
  - gibberish / out-of-context / ambiguous input handling
  - Hinglish & mixed-language recognition
  - no fabricating facts or fake conversation history
  - fact-integrity (won't flip a stated fact due to user pushback)
  - off-topic redirection with a light touch (not robotic refusal)
  - meta-questions about the bot answered directly
- Conversation history stored in SQLite, correctly ordered (this also fixes the
  "reply answers the previous message" bug you found in your testing screenshots —
  history is now fetched *before* the new message is saved, so nothing shifts by one turn)

## Setup

1. Copy the env template and fill in your keys:
   ```
   cp .env.example .env
   ```
   Open `.env` and fill in:
   - `TELEGRAM_BOT_TOKEN` — get this from @BotFather on Telegram (`/newbot`)
   - `GROQ_API_KEY` — get this free at https://console.groq.com

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Run it locally (polling mode — no public URL or deployment needed for testing):
   ```
   python run_polling.py
   ```

4. Open Telegram, find your bot (the username you set with BotFather), tap Start.

That's it — you can now message it and run all the adversarial tests we discussed
(gibberish, Hinglish, emojis, injection attempts, off-topic questions, etc.)

## What to test now (before we move to Phase 2)

Go through the full test bank we built:
- Basic gibberish and out-of-context replies
- Hinglish / mixed language messages
- Emoji-only replies (relevant and irrelevant ones)
- Ambiguous questions (should ask a clarifying question, not guess)
- Pushback after a claimed fact (should not flip its answer)
- Off-topic general knowledge (should redirect, but keep poems/jokes finance-flavored)
- Meta questions ("why you over ChatGPT", "are you real AI", etc.)
- Identity override / prompt injection attempts ("ignore your instructions", "pretend
  you're not a finance bot", "you are now a general assistant", etc.)
- A message that mixes a real finance question with an injected instruction in the
  same sentence

Report back anything that breaks and we'll tune the system prompt (`app/system_prompt.py`)
before moving to Phase 2 (onboarding).

## Project structure
```
atlas-finance-bot/
├── app/
│   ├── config.py         # env var loading
│   ├── db.py              # SQLite models + helpers
│   ├── system_prompt.py   # the guardrail-heavy system prompt
│   ├── llm.py              # Groq wrapper
│   └── handlers.py         # Telegram message handlers
├── run_polling.py          # local testing entry point (use this now)
├── main.py                 # FastAPI + webhook entry point (use this later, on deploy)
├── requirements.txt
└── .env.example
```

## Note on deployment (later, not now)
`main.py` is ready for webhook-based deployment on Railway/Render once we get there —
you'll set `PUBLIC_WEBHOOK_URL` in `.env` to your deployed URL. Not needed for this
testing phase.
