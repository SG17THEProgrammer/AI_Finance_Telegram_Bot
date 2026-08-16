"""
app/services/alert_engine.py

Alert engine for Atlas. Handles:
  - Simple alerts: PRICE_ABOVE, PRICE_BELOW, PERCENT_DROP, PERCENT_GAIN,
                   RSI_OVERSOLD, RSI_OVERBOUGHT
  - Complex alerts: TRAILING_DAYS, LAGGED_PERCENT_DROP
  - Daily baseline reset for recurring index PERCENT_DROP/GAIN alerts

FIX: `period="30d"` used everywhere so trailing/lagged alerts have enough history.
FIX: `_get_current_price` and `price_histories` are now properly defined.
FIX: `reset_daily_baselines()` added — called by scheduler at 9:16 AM IST.
"""

import json
import numpy as np
import yfinance as yf
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

# ── Index ticker aliases ───────────────────────────────────────────────────────
_INDEX_ALIASES = {
    "NIFTY50":    "^NSEI",
    "NIFTY 50":   "^NSEI",
    "NIFTY":      "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "BANK NIFTY": "^NSEBANK",
    "SENSEX":     "^BSESN",
    "NASDAQ100":  "^NDX",
    "NASDAQ 100": "^NDX",
    "NASDAQ":     "^IXIC",
    "SP500":      "^GSPC",
    "S&P500":     "^GSPC",
}


def _resolve_ticker(raw: str) -> str:
    """Resolves user-friendly names to yfinance-compatible symbols."""
    upper = raw.strip().upper()
    return _INDEX_ALIASES.get(upper, upper)


# ── Price fetch helpers ────────────────────────────────────────────────────────

def _get_current_price(ticker: str) -> float | None:
    """
    Fetches the latest available price for a ticker.
    Tries the symbol as-is first (handles ^NSEI, ^GSPC indices),
    then falls back to .NS and .BO for Indian equities.
    Returns None on failure — callers must handle this gracefully.
    """
    candidates = [ticker]
    if not ticker.startswith("^"):
        candidates += [f"{ticker}.NS", f"{ticker}.BO"]

    for candidate in candidates:
        try:
            t = yf.Ticker(candidate)
            hist = t.history(period="5d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception:
            continue
    return None


def _get_price_history(ticker: str, days: int = 30):
    # Map requested days to the nearest valid yfinance period string
    if days <= 5:
        period = "5d"
    elif days <= 21:
        period = "1mo"
    elif days <= 63:
        period = "3mo"
    else:
        period = "6mo"

    candidates = [ticker]
    if not ticker.startswith("^"):
        candidates += [f"{ticker}.NS", f"{ticker}.BO"]

    for candidate in candidates:
        try:
            hist = yf.Ticker(candidate).history(period=period)
            if not hist.empty:
                return hist
        except Exception:
            continue
    return None


def _compute_rsi(close, period: int = 14):
    """Wilder's RSI-14."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── Alert creation ─────────────────────────────────────────────────────────────

def create_alert(db, telegram_id: str, ticker: str, alert_type: str,
                 target_value: str, permanent: bool = False) -> dict:
    """
    Creates a simple alert (PRICE_ABOVE, PRICE_BELOW, PERCENT_DROP, PERCENT_GAIN,
    RSI_OVERSOLD, RSI_OVERBOUGHT).

    Key behaviour:
    - Fetches current price at creation time — returned in the response so the
      LLM can tell the user "baseline is X".
    - If condition is already met → returns already_met=True, no alert created.
    - Index PERCENT_DROP/GAIN alerts are recurring by default (baseline resets daily).
    - permanent=True forces is_recurring=1 on any alert type.
    """
    from app.database.db import Alert

    resolved = _resolve_ticker(ticker)
    current_price = _get_current_price(resolved)

    if current_price is None:
        return {"error": f"Could not fetch current price for '{ticker}'. Check the ticker and try again."}

    today_str = datetime.now(_IST).strftime("%Y-%m-%d")

    # Determine if this should be a recurring alert
    is_index = resolved.startswith("^")
    is_pct_type = alert_type in ("PERCENT_DROP", "PERCENT_GAIN")
    is_recurring = 1 if (permanent or (is_index and is_pct_type)) else 0

    # Check if already met — don't create a useless alert
    already_met = False
    try:
        target = float(target_value.replace("%", ""))
        if alert_type == "PRICE_BELOW" and current_price <= target:
            already_met = True
        elif alert_type == "PRICE_ABOVE" and current_price >= target:
            already_met = True
        elif alert_type == "PERCENT_DROP":
            # Can't be already-met at creation since baseline = current
            pass
        elif alert_type == "RSI_OVERSOLD":
            hist = _get_price_history(resolved, days=30)
            if hist is not None:
                rsi = _compute_rsi(hist["Close"].dropna())
                if not rsi.empty and float(rsi.iloc[-1]) <= target:
                    already_met = True
        elif alert_type == "RSI_OVERBOUGHT":
            hist = _get_price_history(resolved, days=30)
            if hist is not None:
                rsi = _compute_rsi(hist["Close"].dropna())
                if not rsi.empty and float(rsi.iloc[-1]) >= target:
                    already_met = True
    except Exception:
        pass

    if already_met:
        return {
            "already_met": True,
            "current_price": current_price,
            "message": (
                f"The condition is already met right now (current price: {current_price:.2f}). "
                "Please set a different target."
            ),
        }

    alert = Alert(
        telegram_id=str(telegram_id),
        ticker=resolved,
        alert_type=alert_type,
        target_value=str(target_value),
        baseline_price=str(current_price),
        baseline_date=today_str,
        is_recurring=is_recurring,
        armed=1,
        is_active=1,
    )
    db.add(alert)
    db.commit()

    recurring_note = ""
    if is_recurring and is_pct_type:
        recurring_note = " The baseline resets daily at market open, so this always measures today's movement."

    return {
        "created": True,
        "alert_type": alert_type,
        "ticker": ticker,
        "target": target_value,
        "current_price": current_price,
        "is_recurring": bool(is_recurring),
        "note": recurring_note,
    }


def create_trailing_days_alert(db, telegram_id: str, ticker: str,
                                direction: str, trigger_days: int,
                                window_days: int) -> dict:
    """
    Alert fires when ticker has been moving in `direction` for at least
    `trigger_days` out of the last `window_days` trading days.

    Example: "Alert when Nifty trails for 5 out of 8 days"
    → direction='down', trigger_days=5, window_days=8
    """
    from app.database.db import Alert

    resolved = _resolve_ticker(ticker)
    hist = _get_price_history(resolved, days=window_days + 10)

    if hist is None or len(hist) < window_days:
        return {"error": f"Not enough history for '{ticker}' to set up a trailing alert."}

    config = json.dumps({
        "direction": direction.lower(),
        "trigger_days": trigger_days,
        "window_days": window_days,
    })

    alert = Alert(
        telegram_id=str(telegram_id),
        ticker=resolved,
        alert_type="TRAILING_DAYS",
        target_value=f"{trigger_days}/{window_days}",
        baseline_price=None,
        baseline_date=None,
        is_recurring=1,
        armed=1,
        is_active=1,
        extra_config=config,
    )
    db.add(alert)
    db.commit()

    direction_word = "down" if direction == "down" else "up"
    return {
        "created": True,
        "alert_type": "TRAILING_DAYS",
        "ticker": ticker,
        "description": (
            f"Alert set. I'll notify you when {ticker} closes {direction_word} "
            f"for {trigger_days} or more days out of the last {window_days} trading days. "
            "Checked using daily closing prices — recurring, no expiry."
        ),
    }


def create_lagged_percent_alert(db, telegram_id: str, ticker: str,
                                 drop_pct: float, lag_days: int) -> dict:
    """
    Alert fires when ticker drops `drop_pct`% compared to its price
    `lag_days` trading days ago. Rolling window — resets automatically.

    Example: "Alert if Nifty drops 1.5% over 5 days"
    → drop_pct=1.5, lag_days=5
    """
    from app.database.db import Alert

    resolved = _resolve_ticker(ticker)

    config = json.dumps({
        "drop_pct": float(drop_pct),
        "lag_days": int(lag_days),
    })

    alert = Alert(
        telegram_id=str(telegram_id),
        ticker=resolved,
        alert_type="LAGGED_PERCENT_DROP",
        target_value=f"{drop_pct}%/{lag_days}d",
        baseline_price=None,
        baseline_date=None,
        is_recurring=1,
        armed=1,
        is_active=1,
        extra_config=config,
    )
    db.add(alert)
    db.commit()

    return {
        "created": True,
        "alert_type": "LAGGED_PERCENT_DROP",
        "ticker": ticker,
        "description": (
            f"Alert set. I'll notify you when {ticker} drops more than {drop_pct}% "
            f"compared to its price {lag_days} trading days ago. "
            "This is a rolling window — it checks continuously and is recurring."
        ),
    }


# ── Daily baseline reset ───────────────────────────────────────────────────────

def reset_daily_baselines(db) -> int:
    """
    Called by scheduler at 9:16 AM IST on weekdays (just after NSE market open).
    For every active recurring PERCENT_DROP/GAIN alert, fetches today's
    opening price and updates baseline_price + baseline_date.

    This is the fix for the bug where the baseline was stuck at alert-creation
    price instead of resetting to "today's open" each trading day.
    Returns count of baselines reset.
    """
    from app.database.db import Alert

    today_str = datetime.now(_IST).strftime("%Y-%m-%d")

    candidates = db.query(Alert).filter(
        Alert.is_active == 1,
        Alert.is_recurring == 1,
        Alert.alert_type.in_(["PERCENT_DROP", "PERCENT_GAIN"]),
        Alert.baseline_date != today_str,
    ).all()

    reset_count = 0
    price_cache = {}  # avoid fetching same ticker twice

    for alert in candidates:
        ticker = alert.ticker
        if ticker not in price_cache:
            price_cache[ticker] = _get_current_price(ticker)
        price = price_cache[ticker]
        if price is not None:
            alert.baseline_price = str(price)
            alert.baseline_date = today_str
            reset_count += 1

    if reset_count:
        db.commit()
        print(f"[AlertEngine] Reset {reset_count} daily baselines.")

    return reset_count


# ── Check helpers for complex alerts ─────────────────────────────────────────

def _check_trailing_days(alert, price_histories: dict) -> bool:
    """Returns True if the trailing-days condition is met."""
    config = json.loads(alert.extra_config or "{}")
    direction   = config.get("direction", "down")
    trigger_days = int(config.get("trigger_days", 5))
    window_days  = int(config.get("window_days", 8))

    hist = price_histories.get(alert.ticker)
    if hist is None or len(hist) < window_days + 1:
        return False

    closes = hist["Close"].dropna().tail(window_days + 1)
    daily_returns = closes.pct_change().dropna().tail(window_days)

    count = int((daily_returns < 0).sum()) if direction == "down" else int((daily_returns > 0).sum())
    return count >= trigger_days


def _check_lagged_percent(alert, price_histories: dict) -> bool:
    """Returns True if the lagged-percent-drop condition is met."""
    config   = json.loads(alert.extra_config or "{}")
    drop_pct = float(config.get("drop_pct", 1.0))
    lag_days = int(config.get("lag_days", 5))

    hist = price_histories.get(alert.ticker)
    if hist is None or len(hist) < lag_days + 1:
        return False

    closes = hist["Close"].dropna()
    if len(closes) < lag_days + 1:
        return False

    current = float(closes.iloc[-1])
    past    = float(closes.iloc[-(lag_days + 1)])
    if past == 0:
        return False

    actual_drop_pct = ((past - current) / past) * 100
    return actual_drop_pct >= drop_pct


# ── Trigger message formatter ──────────────────────────────────────────────────

def format_trigger_message(alert, current_price: float | None = None) -> str:
    """Formats the push notification sent to the user when an alert fires."""
    ticker_display = alert.ticker.lstrip("^")
    price_str = f"₹{current_price:,.2f}" if current_price else "N/A"
    recurring_note = " 🔄 Still watching." if alert.is_recurring else ""

    base = f"🚨 *Atlas Alert — {ticker_display}*\n\n"

    if alert.alert_type == "PRICE_BELOW":
        return base + f"Price dropped below *{alert.target_value}*\nCurrent: *{price_str}*{recurring_note}"
    if alert.alert_type == "PRICE_ABOVE":
        return base + f"Price rose above *{alert.target_value}*\nCurrent: *{price_str}*{recurring_note}"
    if alert.alert_type == "PERCENT_DROP":
        return base + f"Dropped more than *{alert.target_value}%* from today's open\nCurrent: *{price_str}*{recurring_note}"
    if alert.alert_type == "PERCENT_GAIN":
        return base + f"Gained more than *{alert.target_value}%* from today's open\nCurrent: *{price_str}*{recurring_note}"
    if alert.alert_type == "RSI_OVERSOLD":
        return base + f"RSI dropped into oversold territory (≤ {alert.target_value})\nCurrent: *{price_str}* 🟢 Potential buy zone.{recurring_note}"
    if alert.alert_type == "RSI_OVERBOUGHT":
        return base + f"RSI entered overbought territory (≥ {alert.target_value})\nCurrent: *{price_str}* 🔴 Potential caution zone.{recurring_note}"
    if alert.alert_type == "TRAILING_DAYS":
        cfg = json.loads(alert.extra_config or "{}")
        d = cfg.get("direction", "down")
        t = cfg.get("trigger_days", "?")
        w = cfg.get("window_days", "?")
        return base + f"Trailing condition met: closed *{d}* for *{t}* of last *{w}* trading days. 🔄 Still watching."
    if alert.alert_type == "LAGGED_PERCENT_DROP":
        cfg = json.loads(alert.extra_config or "{}")
        pct = cfg.get("drop_pct", "?")
        lag = cfg.get("lag_days", "?")
        return base + f"Dropped *{pct}%+* vs price *{lag}* trading days ago\nCurrent: *{price_str}* 🔄 Still watching."

    return base + f"Condition met. Current: *{price_str}*{recurring_note}"


# ── Main check loop — called every 15 min by scheduler ────────────────────────

def check_active_alerts(db, bot_send_fn) -> int:
    """
    Checks all active alerts and fires any that meet their conditions.
    `bot_send_fn` is an async coroutine: async def send(telegram_id, text).
    Returns count of alerts triggered this cycle.

    Key fix: fetches period="30d" (not "2d") so TRAILING_DAYS and
    LAGGED_PERCENT_DROP have enough history to evaluate.
    """
    from app.database.db import Alert

    active_alerts = db.query(Alert).filter(Alert.is_active == 1, Alert.armed == 1).all()
    if not active_alerts:
        return 0

    # ── Batch-fetch price history — one yfinance call per unique ticker ──────
    # Using period="30d" so complex alerts have enough daily close data.
    unique_tickers = list({a.ticker for a in active_alerts})
    price_histories = {}   # ticker → DataFrame
    current_prices  = {}   # ticker → float (latest close)

    for ticker in unique_tickers:
        hist = _get_price_history(ticker, days=30)  # ← "30d" not "2d"
        if hist is not None and not hist.empty:
            price_histories[ticker] = hist
            current_prices[ticker] = float(hist["Close"].iloc[-1])

    triggered_count = 0

    for alert in active_alerts:
        ticker = alert.ticker
        current = current_prices.get(ticker)
        triggered = False

        try:
            if alert.alert_type == "PRICE_BELOW":
                target = float(alert.target_value)
                triggered = current is not None and current <= target

            elif alert.alert_type == "PRICE_ABOVE":
                target = float(alert.target_value)
                triggered = current is not None and current >= target

            elif alert.alert_type in ("PERCENT_DROP", "PERCENT_GAIN"):
                if alert.baseline_price and current is not None:
                    baseline = float(alert.baseline_price)
                    pct_change = ((current - baseline) / baseline) * 100
                    target_pct = float(alert.target_value.replace("%", ""))
                    if alert.alert_type == "PERCENT_DROP":
                        triggered = pct_change <= -target_pct
                    else:
                        triggered = pct_change >= target_pct

            elif alert.alert_type == "RSI_OVERSOLD":
                hist = price_histories.get(ticker)
                if hist is not None:
                    rsi = _compute_rsi(hist["Close"].dropna())
                    if not rsi.empty:
                        triggered = float(rsi.iloc[-1]) <= float(alert.target_value)

            elif alert.alert_type == "RSI_OVERBOUGHT":
                hist = price_histories.get(ticker)
                if hist is not None:
                    rsi = _compute_rsi(hist["Close"].dropna())
                    if not rsi.empty:
                        triggered = float(rsi.iloc[-1]) >= float(alert.target_value)

            elif alert.alert_type == "TRAILING_DAYS":
                triggered = _check_trailing_days(alert, price_histories)

            elif alert.alert_type == "LAGGED_PERCENT_DROP":
                triggered = _check_lagged_percent(alert, price_histories)

        except Exception as exc:
            print(f"[AlertEngine] Error checking alert {alert.id}: {exc}")
            continue

        if triggered:
            msg = format_trigger_message(alert, current)
            bot_send_fn(alert.telegram_id, msg)  # scheduler calls this
            triggered_count += 1

            if alert.is_recurring:
                # Re-arm for next check; for RSI alerts, disarm until RSI exits zone
                if alert.alert_type in ("RSI_OVERSOLD", "RSI_OVERBOUGHT", "TRAILING_DAYS", "LAGGED_PERCENT_DROP"):
                    alert.armed = 0  # disarmed until RSI normalises
            else:
                alert.is_active = 0  # one-shot: deactivate after firing

            alert.triggered_at = datetime.now(timezone.utc)

    db.commit()
    return triggered_count


def re_arm_recurring_alerts(db) -> int:
    """
    Re-arms recurring alerts that have returned to a neutral state.
    Applies a strict 24-hour cooldown to prevent intraday flapping spam.
    """
    from app.database.db import Alert
    from datetime import datetime, timezone

    disarmed = db.query(Alert).filter(
        Alert.is_active == 1,
        Alert.armed == 0,
        Alert.alert_type.in_(["RSI_OVERSOLD", "RSI_OVERBOUGHT", "TRAILING_DAYS", "LAGGED_PERCENT_DROP"]),
    ).all()

    if not disarmed:
        return 0

    # Batch-fetch price history for the disarmed alerts
    unique_tickers = list({a.ticker for a in disarmed})
    price_histories = {}
    for ticker in unique_tickers:
        hist = _get_price_history(ticker, days=30)
        if hist is not None and not hist.empty:
            price_histories[ticker] = hist

    re_armed = 0
    # Strip timezone to avoid naive vs aware datetime math errors in SQLite
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    for alert in disarmed:
        # ── ANTI-FLAP COOLDOWN FIX ──
        # Prevent re-arming if the alert triggered within the last 24 hours.
        # This stops 15-minute spam loops caused by live market fluctuations.
        if alert.triggered_at:
            triggered_naive = alert.triggered_at.replace(tzinfo=None)
            hours_since = (now_utc_naive - triggered_naive).total_seconds() / 3600.0
            if hours_since < 24:
                continue  # Skip this alert, it triggered too recently

        hist = price_histories.get(alert.ticker)
        if hist is None:
            continue

        try:
            if alert.alert_type == "RSI_OVERSOLD":
                rsi = _compute_rsi(hist["Close"].dropna())
                if not rsi.empty and float(rsi.iloc[-1]) > 35:
                    alert.armed = 1
                    re_armed += 1

            elif alert.alert_type == "RSI_OVERBOUGHT":
                rsi = _compute_rsi(hist["Close"].dropna())
                if not rsi.empty and float(rsi.iloc[-1]) < 65:
                    alert.armed = 1
                    re_armed += 1

            elif alert.alert_type == "TRAILING_DAYS":
                if not _check_trailing_days(alert, price_histories):
                    alert.armed = 1
                    re_armed += 1

            elif alert.alert_type == "LAGGED_PERCENT_DROP":
                if not _check_lagged_percent(alert, price_histories):
                    alert.armed = 1
                    re_armed += 1

        except Exception as exc:
            print(f"[AlertEngine] Error re-arming alert {alert.id}: {exc}")
            continue

    if re_armed:
        db.commit()
    return re_armed

def list_alerts(db, telegram_id: str, active_only: bool = True) -> list:
    """Returns alerts for a user. Used by the list_market_alerts tool."""
    from app.database.db import Alert
    query = db.query(Alert).filter(Alert.telegram_id == str(telegram_id))
    if active_only:
        query = query.filter(Alert.is_active == 1)
    alerts = query.order_by(Alert.created_at.desc()).all()
    return [
        {
            "id": a.id,
            "ticker": a.ticker,
            "alert_type": a.alert_type,
            "target_value": a.target_value,
            "is_recurring": bool(a.is_recurring),
            "baseline_price": a.baseline_price,
            "created_at": str(a.created_at),
        }
        for a in alerts
    ]