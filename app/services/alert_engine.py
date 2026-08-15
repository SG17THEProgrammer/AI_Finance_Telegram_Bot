"""
Threshold alert engine.

Responsible for:
  - creating alerts (with a price snapshot for percent-based alerts)
  - evaluating active alerts against live market data
  - returning trigger events for the scheduler to push as Telegram messages

Hard rule, same as financial_data.py: never fabricate a price or RSI value.
If a symbol can't be resolved or yfinance returns nothing usable, the alert
is simply skipped this cycle (not silently marked triggered) and logged.

Index alerts (Nifty 50, Nasdaq 100, Sensex, Bank Nifty) are NOT a separate
alert_type - they reuse PERCENT_DROP/PERCENT_GAIN, just with the ticker
resolved to its yfinance index symbol (e.g. "NIFTY50" -> "^NSEI") instead of
an equity symbol. This keeps the schema and evaluation logic in one place
rather than duplicating it for "stock" vs "index".
"""

import logging
import yfinance as yf
import pandas as pd

from app.services.financial_data import _normalize_symbol

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

VALID_ALERT_TYPES = {
    "PRICE_ABOVE",
    "PRICE_BELOW",
    "PERCENT_DROP",
    "PERCENT_GAIN",
    "RSI_OVERSOLD",
    "RSI_OVERBOUGHT",
}

# Index aliases resolve to their yfinance ticker symbols. Not exhaustive -
# covers the ones mentioned in the brainstorming notes (Nifty 50, Nasdaq 100)
# plus the other two obvious Indian benchmarks.
INDEX_ALIASES = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "NIFTY 50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "BANK NIFTY": "^NSEBANK",
    "SENSEX": "^BSESN",
    "NASDAQ": "^NDX",
    "NASDAQ100": "^NDX",
    "NASDAQ 100": "^NDX",
}

PERCENT_TYPES = {"PERCENT_DROP", "PERCENT_GAIN"}
RSI_TYPES = {"RSI_OVERSOLD", "RSI_OVERBOUGHT"}


def _is_index(ticker: str) -> bool:
    """True when the alert ticker maps to a yfinance index symbol."""
    key = ticker.strip().upper()
    return key in INDEX_ALIASES or _resolve_symbol(key).startswith("^")


def _today_ist_str() -> str:
    """Return today's date in ISO format for IST timezone."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")


def _resolve_symbol(ticker: str) -> str:
    """Returns the actual yfinance-fetchable symbol for a user-given ticker,
    checking index aliases first, then falling back to the equity alias map."""
    key = ticker.strip().upper()
    if key in INDEX_ALIASES:
        return INDEX_ALIASES[key]
    return _normalize_symbol(ticker)


def _compute_rsi(close_series: pd.Series, period: int = 14):
    """Standard 14-period RSI off a closing-price series. Returns None if
    there isn't enough history to compute it (never guesses a value)."""
    if len(close_series) < period + 1:
        return None
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    last_avg_gain = avg_gain.iloc[-1]
    last_avg_loss = avg_loss.iloc[-1]
    if last_avg_loss == 0:
        return 100.0
    rs = last_avg_gain / last_avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def get_price_and_rsi(ticker: str) -> dict:
    """
    Single-call fetch of current price + RSI-14 for a symbol. Tries the
    resolved symbol as-is (covers indices and US tickers), then NSE/BSE
    suffixes for Indian equities - same fallback order as financial_data.py.
    Returns {"error": ...} if nothing could be fetched anywhere.
    """
    symbol = _resolve_symbol(ticker)
    candidates = [symbol] if symbol.startswith("^") else [symbol, f"{symbol}.NS", f"{symbol}.BO"]

    for candidate in candidates:
        try:
            hist = yf.Ticker(candidate).history(period="2mo", interval="1d")
        except Exception:
            continue
        if hist.empty or "Close" not in hist:
            continue
        close = hist["Close"].dropna()
        if close.empty:
            continue
        price = round(float(close.iloc[-1]), 2)
        rsi = _compute_rsi(close)
        return {"symbol": candidate, "price": price, "rsi": rsi}

    return {"error": f"Could not fetch market data for '{ticker}'."}


def create_alert(db, telegram_id: str, ticker: str, alert_type: str, target_value: str, permanent: bool = False) -> dict:

    from app.database.db import Alert

    alert_type = alert_type.strip().upper()
    if alert_type not in VALID_ALERT_TYPES:
        return {"error": f"Unknown alert type '{alert_type}'. Must be one of: {', '.join(sorted(VALID_ALERT_TYPES))}"}

    try:
        target_num = float(str(target_value).replace("%", "").strip())
    except ValueError:
        return {"error": f"target_value must be numeric, got '{target_value}'."}

    ticker_clean = ticker.strip().upper()
    is_index = _is_index(ticker_clean)
    is_recurring = alert_type in RSI_TYPES or (alert_type in PERCENT_TYPES and is_index)

    # Allow user to explicitly request a permanent/recurring watch for any type
    if str(permanent).lower() in ("true", "1", "yes"):
        is_recurring = True

    # Always fetch current price — needed as baseline for PERCENT alerts,
    # and returned to the LLM so it can tell the user the current level
    # regardless of alert type (PRICE_BELOW at 1350 when price is 1310 is
    # a meaningful signal the user should see, not silently skip).
    quote = get_price_and_rsi(ticker)
    if "error" in quote:
        return quote

    current_price = quote["price"]
    current_rsi = quote.get("rsi")

    # Check if the condition is already met right now — creating an alert
    # that would fire instantly (or in the next 10-min cycle) with no real
    # watch period is almost never what the user meant. Return a clear flag
    # so the LLM can inform them rather than silently setting a useless alert.
    instant_trigger = False
    if alert_type == "PRICE_BELOW" and current_price <= target_num:
        instant_trigger = True
    elif alert_type == "PRICE_ABOVE" and current_price >= target_num:
        instant_trigger = True
    elif alert_type == "RSI_OVERSOLD" and current_rsi is not None and current_rsi <= target_num:
        instant_trigger = True
    elif alert_type == "RSI_OVERBOUGHT" and current_rsi is not None and current_rsi >= target_num:
        instant_trigger = True

    if instant_trigger:
        return {
            "already_met": True,
            "ticker": ticker_clean,
            "alert_type": alert_type,
            "target_value": target_num,
            "current_price": current_price,
            "current_rsi": current_rsi,
        }

    # Condition is not yet met — safe to create the alert
    baseline_price = None
    baseline_date = None
    if alert_type in PERCENT_TYPES:
        baseline_price = current_price
        baseline_date = _today_ist_str()

    alert = Alert(
        telegram_id=telegram_id,
        ticker=ticker_clean,
        alert_type=alert_type,
        target_value=str(target_num),
        baseline_price=baseline_price,
        baseline_date=baseline_date,
        is_recurring=1 if is_recurring else 0,
        armed=1,
        is_active=1,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)

    return {
        "success": True,
        "alert_id": alert.id,
        "ticker": alert.ticker,
        "alert_type": alert_type,
        "target_value": target_num,
        "current_price": current_price,
        "current_rsi": current_rsi,
        "baseline_price": baseline_price,
        "is_recurring": is_recurring,
    }


def list_alerts(db, telegram_id: str, active_only: bool = True) -> list:
    from app.database.db import Alert

    q = db.query(Alert).filter(Alert.telegram_id == telegram_id)
    if active_only:
        q = q.filter(Alert.is_active == 1)
    return q.order_by(Alert.created_at.desc()).all()


def delete_alert(db, telegram_id: str, alert_id: int) -> dict:
    from app.database.db import Alert

    alert = db.query(Alert).filter(Alert.id == alert_id, Alert.telegram_id == telegram_id).first()
    if not alert:
        return {"error": "Alert not found."}
    db.delete(alert)
    db.commit()
    return {"success": True}


def _alert_condition_met(alert, price: float, rsi) -> bool:
    target = float(alert.target_value)

    if alert.alert_type == "PRICE_ABOVE":
        return price >= target
    if alert.alert_type == "PRICE_BELOW":
        return price <= target
    if alert.alert_type == "PERCENT_DROP":
        if not alert.baseline_price:
            return False
        pct_change = (price - alert.baseline_price) / alert.baseline_price * 100
        return pct_change <= -target
    if alert.alert_type == "PERCENT_GAIN":
        if not alert.baseline_price:
            return False
        pct_change = (price - alert.baseline_price) / alert.baseline_price * 100
        return pct_change >= target
    if alert.alert_type == "RSI_OVERSOLD":
        return rsi is not None and rsi <= target
    if alert.alert_type == "RSI_OVERBOUGHT":
        return rsi is not None and rsi >= target
    return False


def check_active_alerts(db) -> list:
    """
    Evaluates every active alert against live data. Returns a list of dicts
    for alerts that triggered this cycle - the caller (scheduler) is
    responsible for sending the Telegram message and this function marks
    the alert inactive + stamps triggered_at so it fires once, not every
    cycle. Alerts whose ticker can't be fetched this cycle are left active
    and simply skipped - a transient data-source hiccup should never
    silently disable a user's alert.
    """
    from datetime import datetime, timezone
    from app.database.db import Alert

    triggered = []
    active_alerts = db.query(Alert).filter(Alert.is_active == 1).all()

    # Group by ticker so we only fetch each symbol once per cycle, even if
    # multiple users/alerts watch the same stock or index.
    tickers = {a.ticker for a in active_alerts}
    quotes = {}
    for ticker in tickers:
        quotes[ticker] = get_price_and_rsi(ticker)

    for alert in active_alerts:
        quote = quotes.get(alert.ticker, {})
        if "error" in quote:
            continue

        price = quote["price"]
        rsi = quote.get("rsi")

        if _alert_condition_met(alert, price, rsi):
            alert.is_active = 0
            alert.triggered_at = datetime.now(timezone.utc)
            triggered.append({
                "telegram_id": alert.telegram_id,
                "ticker": alert.ticker,
                "alert_type": alert.alert_type,
                "target_value": float(alert.target_value),
                "baseline_price": alert.baseline_price,
                "current_price": price,
                "rsi": rsi,
            })

    if triggered:
        db.commit()

    return triggered


def format_trigger_message(event: dict) -> str:
    """Builds the human-readable Telegram push message for a triggered alert."""
    ticker = event["ticker"]
    price = event["current_price"]
    alert_type = event["alert_type"]

    if alert_type == "PRICE_ABOVE":
        return f"🚨 *{ticker}* has crossed above ₹{event['target_value']:.2f} — now at ₹{price:.2f}."
    if alert_type == "PRICE_BELOW":
        return f"🚨 *{ticker}* has dropped below ₹{event['target_value']:.2f} — now at ₹{price:.2f}."
    if alert_type == "PERCENT_DROP":
        return (
            f"🚨 *{ticker}* has fallen {event['target_value']:.1f}%+ from ₹{event['baseline_price']:.2f} "
            f"— now at ₹{price:.2f}."
        )
    if alert_type == "PERCENT_GAIN":
        return (
            f"🚀 *{ticker}* has risen {event['target_value']:.1f}%+ from ₹{event['baseline_price']:.2f} "
            f"— now at ₹{price:.2f}."
        )
    if alert_type == "RSI_OVERSOLD":
        return f"📉 *{ticker}* is now technically oversold (RSI {event['rsi']:.1f}) — potential buying zone."
    if alert_type == "RSI_OVERBOUGHT":
        return f"📈 *{ticker}* is now technically overbought (RSI {event['rsi']:.1f})."
    return f"🚨 Alert triggered for *{ticker}*."