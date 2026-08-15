"""
app/services/chart_engine.py

Analytically meaningful chart types for Atlas. Each function generates a
chart as chart_{telegram_id}.png and returns a success/error dict — the same
pattern used in financial_data.py so handlers.py picks them up automatically
via the existing chart_path check.

Charts:
  1. generate_candlestick_with_ma   — candles + 50-day & 200-day MA
  2. generate_rsi_gauge             — RSI speedometer + recent price chart
  3. generate_sector_heatmap        — NSE sector performance grid (day's %)
  4. generate_fundamental_radar     — P/E, P/B, ROE vs industry avg radar
  5. generate_support_resistance    — price chart with auto S/R lines
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # CRITICAL: prevents crash on Linux/Railway
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyArrowPatch
import mplfinance as mpf
import yfinance as yf

from app.services.financial_data import _normalize_symbol


# ── Shared helpers ────────────────────────────────────────────────────────────

def _purge_chart(chart_path: str):
    """Delete leftover chart file and close all matplotlib figures."""
    if os.path.exists(chart_path):
        try:
            os.remove(chart_path)
        except Exception:
            pass
    plt.close('all')


def _fetch_history(symbol: str, period: str = "6mo") -> pd.DataFrame:
    """Fetches OHLCV history, tries NSE fallback for Indian tickers."""
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period)
    if hist.empty:
        ticker = yf.Ticker(f"{symbol}.NS")
        hist = ticker.history(period=period)
    return hist


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI — same method used in alert_engine.py."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── 1. Candlestick + 50-day & 200-day Moving Averages ────────────────────────

def generate_candlestick_with_ma(query: str, telegram_id: str, period: str = "6mo") -> dict:
    """
    Professional candlestick chart overlaid with 50-day and 200-day moving
    averages. The MA crossover (Golden Cross / Death Cross) is the single most
    watched signal by institutional traders — this makes it visible at a glance.
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    symbol = _normalize_symbol(query)
    # Need enough history to compute 200-day MA even if user asks for 6mo view
    hist = _fetch_history(symbol, period="1y")

    if hist.empty:
        return {"error": f"Could not fetch price history for '{query}'. The ticker may be unavailable."}

    # Strip timezone so mplfinance doesn't complain
    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    # Compute MAs on full 1y data, then slice to requested period for display
    hist['MA50'] = hist['Close'].rolling(window=50).mean()
    hist['MA200'] = hist['Close'].rolling(window=200).mean()

    period_map = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252, "2y": 504}
    display_rows = period_map.get(period, 126)
    hist_display = hist.tail(display_rows).copy()

    # Build addplots for the MAs
    ap = []
    if hist_display['MA50'].notna().any():
        ap.append(mpf.make_addplot(hist_display['MA50'], color='#2196F3', width=1.5, label='MA50'))
    if hist_display['MA200'].notna().any():
        ap.append(mpf.make_addplot(hist_display['MA200'], color='#FF9800', width=1.5, label='MA200'))

    mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', inherit=True)
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle='--', gridaxis='both',
                           facecolor='#1a1a2e', figcolor='#1a1a2e',
                           rc={'axes.labelcolor': 'white', 'xtick.color': 'white', 'ytick.color': 'white'})

    fig, axes = mpf.plot(
        hist_display,
        type='candle',
        style=s,
        title=f"\n{symbol} — Candlestick + MA50 & MA200 ({period})",
        ylabel='Price',
        addplot=ap if ap else None,
        volume=True,
        returnfig=True,
        figsize=(12, 7),
    )

    # Manual legend since mplfinance addplot labels aren't auto-rendered
    legend_patches = []
    if hist_display['MA50'].notna().any():
        legend_patches.append(mpatches.Patch(color='#2196F3', label='50-day MA'))
    if hist_display['MA200'].notna().any():
        legend_patches.append(mpatches.Patch(color='#FF9800', label='200-day MA'))
    if legend_patches:
        axes[0].legend(handles=legend_patches, loc='upper left',
                       facecolor='#1a1a2e', labelcolor='white', fontsize=9)

    fig.savefig(chart_path, dpi=110, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close('all')

    return {
        "success": (
            f"Candlestick chart with 50-day and 200-day Moving Averages for {symbol} "
            f"over {period} has been generated and is attached below. "
            "A Golden Cross (MA50 crossing above MA200) signals bullish momentum; "
            "a Death Cross signals bearish momentum."
        )
    }


# ── 2. RSI Gauge + Price Panel ────────────────────────────────────────────────

def generate_rsi_gauge(query: str, telegram_id: str, period: str = "3mo") -> dict:
    """
    Two-panel chart: top = RSI line with overbought/oversold bands,
    bottom = price line for context. The RSI needle panel makes it immediately
    obvious whether a stock is in buying territory (< 30) or overextended (> 70).
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    symbol = _normalize_symbol(query)
    hist = _fetch_history(symbol, period=period)

    if hist.empty:
        return {"error": f"Could not fetch price history for '{query}'."}

    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    close = hist['Close'].dropna()
    rsi = _compute_rsi(close).dropna()

    if rsi.empty:
        return {"error": f"Not enough price history to compute RSI for '{query}' (need at least 14 days)."}

    current_rsi = round(float(rsi.iloc[-1]), 1)

    # Determine zone
    if current_rsi < 30:
        zone = "OVERSOLD 🟢 — Potential buying opportunity"
        needle_color = '#26a69a'
    elif current_rsi > 70:
        zone = "OVERBOUGHT 🔴 — Potential selling/caution zone"
        needle_color = '#ef5350'
    else:
        zone = "NEUTRAL ⚪ — No extreme signal"
        needle_color = '#FFC107'

    fig = plt.figure(figsize=(12, 7), facecolor='#1a1a2e')
    gs = gridspec.GridSpec(2, 1, height_ratios=[1, 1.4], hspace=0.35, figure=fig)

    # ── Top panel: RSI line chart ──
    ax_rsi = fig.add_subplot(gs[0])
    ax_rsi.set_facecolor('#0d0d1a')
    ax_rsi.plot(rsi.index, rsi.values, color=needle_color, linewidth=1.8, label='RSI-14')
    ax_rsi.axhline(70, color='#ef5350', linestyle='--', linewidth=1, alpha=0.8, label='Overbought (70)')
    ax_rsi.axhline(30, color='#26a69a', linestyle='--', linewidth=1, alpha=0.8, label='Oversold (30)')
    ax_rsi.fill_between(rsi.index, rsi.values, 70, where=(rsi.values > 70), alpha=0.2, color='#ef5350')
    ax_rsi.fill_between(rsi.index, rsi.values, 30, where=(rsi.values < 30), alpha=0.2, color='#26a69a')
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel('RSI', color='white', fontsize=10)
    ax_rsi.tick_params(colors='white')
    ax_rsi.set_title(f'{symbol} — RSI Gauge   |   Current RSI: {current_rsi}   |   {zone}',
                     color='white', fontsize=11, pad=10)
    ax_rsi.legend(loc='upper left', facecolor='#1a1a2e', labelcolor='white', fontsize=8)
    ax_rsi.grid(True, linestyle='--', alpha=0.3, color='gray')

    # ── Bottom panel: price line ──
    ax_price = fig.add_subplot(gs[1])
    ax_price.set_facecolor('#0d0d1a')
    ax_price.plot(close.index, close.values, color='#90CAF9', linewidth=1.5)
    ax_price.set_ylabel('Price', color='white', fontsize=10)
    ax_price.tick_params(colors='white')
    ax_price.set_xlabel('Date', color='white', fontsize=9)
    ax_price.grid(True, linestyle='--', alpha=0.3, color='gray')

    for ax in [ax_rsi, ax_price]:
        for spine in ax.spines.values():
            spine.set_edgecolor('#333355')

    fig.savefig(chart_path, dpi=110, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close('all')

    return {
        "success": (
            f"RSI Gauge chart for {symbol} has been generated and is attached below. "
            f"Current RSI is {current_rsi} — {zone}."
        )
    }


# ── 3. Sector Heatmap ─────────────────────────────────────────────────────────

# Representative ETFs/proxies for Indian NSE sectors + key US sectors
_SECTOR_TICKERS = {
    # Indian sectors via sector ETFs / representative large-caps
    "IT": "TCS.NS",
    "Banking": "HDFCBANK.NS",
    "Auto": "TATAMOTORS.NS",
    "Pharma": "SUNPHARMA.NS",
    "FMCG": "HINDUNILVR.NS",
    "Energy": "RELIANCE.NS",
    "Metals": "TATASTEEL.NS",
    "Infra": "LT.NS",
    # Broad index
    "Nifty50": "^NSEI",
    "BankNifty": "^NSEBANK",
    # US benchmarks for context
    "S&P500": "^GSPC",
    "Nasdaq": "^IXIC",
}


def generate_sector_heatmap(telegram_id: str) -> dict:
    """
    Color-coded grid showing today's % change for key Indian sectors and
    major indices. Green = up, Red = down, intensity reflects magnitude.
    Gives a full market overview at a glance.
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    perf = {}
    for sector, ticker in _SECTOR_TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="2d")
            if len(hist) >= 2:
                prev = float(hist['Close'].iloc[-2])
                curr = float(hist['Close'].iloc[-1])
                pct = ((curr - prev) / prev) * 100 if prev else 0
                perf[sector] = round(pct, 2)
            elif len(hist) == 1:
                perf[sector] = 0.0
        except Exception:
            perf[sector] = None

    # Filter out failed fetches
    perf = {k: v for k, v in perf.items() if v is not None}

    if not perf:
        return {"error": "Could not fetch sector data right now. Market data APIs may be rate-limited."}

    labels = list(perf.keys())
    values = list(perf.values())
    n = len(labels)

    # Build grid dimensions
    cols = 4
    rows = (n + cols - 1) // cols

    fig, ax = plt.subplots(figsize=(12, max(4, rows * 2.2)), facecolor='#1a1a2e')
    ax.set_facecolor('#1a1a2e')
    ax.axis('off')
    ax.set_title("📊 Market Sector Heatmap — Today's Performance",
                 color='white', fontsize=14, fontweight='bold', pad=15)

    max_abs = max(abs(v) for v in values) or 1

    for i, (label, val) in enumerate(zip(labels, values)):
        row, col = divmod(i, cols)
        x = col / cols
        y = 1 - (row + 1) / rows

        # Color intensity proportional to magnitude
        intensity = min(abs(val) / max_abs, 1.0)
        if val > 0:
            color = (0.15 + 0.1 * intensity, 0.5 + 0.45 * intensity, 0.15 + 0.1 * intensity)
        elif val < 0:
            color = (0.5 + 0.45 * intensity, 0.1, 0.1)
        else:
            color = (0.35, 0.35, 0.45)

        cell_width = 0.88 / cols
        cell_height = 0.78 / rows

        rect = plt.Rectangle((x + 0.01, y + 0.01), cell_width, cell_height,
                              transform=ax.transAxes, color=color,
                              linewidth=1.5, edgecolor='#1a1a2e')
        ax.add_patch(rect)

        sign = "+" if val > 0 else ""
        ax.text(x + cell_width / 2 + 0.01, y + cell_height * 0.62 + 0.01,
                label, transform=ax.transAxes,
                ha='center', va='center', color='white', fontsize=10, fontweight='bold')
        ax.text(x + cell_width / 2 + 0.01, y + cell_height * 0.28 + 0.01,
                f"{sign}{val:.2f}%", transform=ax.transAxes,
                ha='center', va='center', color='white', fontsize=11, fontweight='bold')

    fig.savefig(chart_path, dpi=110, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close('all')

    top_gainer = max(perf, key=perf.get)
    top_loser = min(perf, key=perf.get)

    return {
        "success": (
            f"Sector heatmap has been generated and is attached below. "
            f"Best performer: {top_gainer} ({perf[top_gainer]:+.2f}%). "
            f"Worst performer: {top_loser} ({perf[top_loser]:+.2f}%)."
        )
    }


# ── 4. Fundamental Radar Chart ───────────────────────────────────────────────

def generate_fundamental_radar(query: str, telegram_id: str) -> dict:
    """
    Radar (spider) chart comparing a stock's P/E, P/B, ROE, Profit Margin,
    and Debt/Equity against approximate Indian market averages.
    Makes valuation gaps visually obvious — a stock hugging the center on
    all axes vs the average ring is cheap; bulging out means premium pricing.
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    symbol = _normalize_symbol(query)
    info = None

    for candidate in [f"{symbol}.NS", f"{symbol}.BO", symbol]:
        try:
            t = yf.Ticker(candidate)
            info = t.info
            if info and info.get("longName"):
                break
        except Exception:
            continue

    if not info or not info.get("longName"):
        return {"error": f"Could not fetch fundamental data for '{query}'."}

    # Metrics to plot — use 0 if missing (not ideal but avoids crashes)
    metrics = {
        "P/E Ratio": (info.get("trailingPE"), 22.0),        # stock value, market avg
        "P/B Ratio": (info.get("priceToBook"), 3.5),
        "ROE (%)": (
            (info.get("returnOnEquity") or 0) * 100,
            15.0
        ),
        "Profit\nMargin (%)": (
            (info.get("profitMargins") or 0) * 100,
            12.0
        ),
        "Rev Growth (%)": (
            (info.get("revenueGrowth") or 0) * 100,
            10.0
        ),
    }

    labels = list(metrics.keys())
    stock_vals_raw = [v[0] if v[0] is not None else 0 for v in metrics.values()]
    avg_vals_raw = [v[1] for v in metrics.values()]

    # Normalize each metric to 0–100 scale for the radar shape
    # We normalize relative to 2x the market average
    def _normalize(val, avg):
        scale = avg * 2 if avg != 0 else 1
        return min(max((val / scale) * 100, 0), 100)

    stock_vals = [_normalize(sv, av) for sv, av in zip(stock_vals_raw, avg_vals_raw)]
    avg_vals = [_normalize(av, av) for av in avg_vals_raw]  # always 50 after normalization

    N = len(labels)
    angles = [n / float(N) * 2 * np.pi for n in range(N)]
    angles += angles[:1]  # close the polygon

    stock_vals_plot = stock_vals + stock_vals[:1]
    avg_vals_plot = avg_vals + avg_vals[:1]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True), facecolor='#1a1a2e')
    ax.set_facecolor('#0d0d1a')

    ax.plot(angles, stock_vals_plot, 'o-', linewidth=2, color='#2196F3', label=symbol)
    ax.fill(angles, stock_vals_plot, alpha=0.25, color='#2196F3')

    ax.plot(angles, avg_vals_plot, 'o--', linewidth=1.5, color='#FFC107', label='Market Avg')
    ax.fill(angles, avg_vals_plot, alpha=0.1, color='#FFC107')

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, color='white', fontsize=9)
    ax.set_yticklabels([])
    ax.set_ylim(0, 100)
    ax.grid(color='gray', linestyle='--', alpha=0.3)
    ax.spines['polar'].set_color('#333355')

    company_name = info.get("longName", symbol)
    ax.set_title(f"{company_name}\nFundamental Radar vs Market Average",
                 color='white', fontsize=12, fontweight='bold', pad=20)
    ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1),
              facecolor='#1a1a2e', labelcolor='white', fontsize=9)

    # Annotate actual values below the chart
    actuals = []
    for label, (sv, av) in zip(labels, metrics.values()):
        clean_label = label.replace('\n', ' ')
        sv_str = f"{sv:.1f}" if sv is not None else "N/A"
        actuals.append(f"{clean_label}: {sv_str} (Avg: {av})")

    fig.text(0.5, 0.02, "   |   ".join(actuals), ha='center', color='#B0BEC5',
             fontsize=7.5, style='italic', wrap=True)

    fig.savefig(chart_path, dpi=110, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close('all')

    # Build a plain-text summary for the LLM to cite
    summary_lines = []
    for label, (sv, av) in zip(labels, metrics.values()):
        clean = label.replace('\n', ' ')
        sv_str = f"{sv:.1f}" if sv is not None else "N/A"
        comparison = "above" if (sv or 0) > av else "below"
        summary_lines.append(f"{clean}: {sv_str} ({comparison} market avg of {av})")

    return {
        "success": (
            f"Fundamental radar chart for {company_name} has been generated and is attached below. "
            + " | ".join(summary_lines)
        )
    }


# ── 5. Support / Resistance Line Chart ────────────────────────────────────────

def generate_support_resistance(query: str, telegram_id: str, period: str = "6mo") -> dict:
    """
    Price line chart with auto-detected horizontal support and resistance
    levels. Levels are found by identifying local price extremes (peaks and
    troughs) — the most common way traders spot entry/exit zones.
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    symbol = _normalize_symbol(query)
    hist = _fetch_history(symbol, period=period)

    if hist.empty:
        return {"error": f"Could not fetch price history for '{query}'."}

    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    close = hist['Close'].dropna()

    # ── Detect S/R levels via local extremes ──
    # A local high = value higher than N neighbors on both sides
    window = max(5, len(close) // 20)
    levels = []

    for i in range(window, len(close) - window):
        slice_ = close.iloc[i - window: i + window + 1]
        val = float(close.iloc[i])
        if val == float(slice_.max()):
            levels.append(('resistance', val))
        elif val == float(slice_.min()):
            levels.append(('support', val))

    # Deduplicate: merge levels within 1.5% of each other
    def _cluster(raw_levels, threshold=0.015):
        clustered = []
        for kind, val in sorted(raw_levels, key=lambda x: x[1]):
            merged = False
            for j, (ck, cv) in enumerate(clustered):
                if abs(val - cv) / cv < threshold:
                    clustered[j] = (ck, (cv + val) / 2)
                    merged = True
                    break
            if not merged:
                clustered.append((kind, val))
        return clustered

    levels = _cluster(levels)

    # Keep top 3 support + top 3 resistance levels closest to current price
    current_price = float(close.iloc[-1])
    supports = sorted([(k, v) for k, v in levels if k == 'support' and v < current_price],
                      key=lambda x: abs(x[1] - current_price))[:3]
    resistances = sorted([(k, v) for k, v in levels if k == 'resistance' and v > current_price],
                         key=lambda x: abs(x[1] - current_price))[:3]

    fig, ax = plt.subplots(figsize=(12, 6), facecolor='#1a1a2e')
    ax.set_facecolor('#0d0d1a')

    ax.plot(close.index, close.values, color='#90CAF9', linewidth=1.8, label='Price', zorder=3)

    for _, val in supports:
        ax.axhline(val, color='#26a69a', linestyle='--', linewidth=1.2, alpha=0.85)
        ax.text(close.index[-1], val, f"  S {val:,.1f}", color='#26a69a',
                fontsize=8, va='center')

    for _, val in resistances:
        ax.axhline(val, color='#ef5350', linestyle='--', linewidth=1.2, alpha=0.85)
        ax.text(close.index[-1], val, f"  R {val:,.1f}", color='#ef5350',
                fontsize=8, va='center')

    ax.set_title(f"{symbol} — Auto Support & Resistance Levels ({period})",
                 color='white', fontsize=12, fontweight='bold')
    ax.set_ylabel('Price', color='white', fontsize=10)
    ax.set_xlabel('Date', color='white', fontsize=9)
    ax.tick_params(colors='white')
    ax.grid(True, linestyle='--', alpha=0.25, color='gray')
    for spine in ax.spines.values():
        spine.set_edgecolor('#333355')

    s_patch = mpatches.Patch(color='#26a69a', label='Support')
    r_patch = mpatches.Patch(color='#ef5350', label='Resistance')
    price_patch = mpatches.Patch(color='#90CAF9', label='Price')
    ax.legend(handles=[price_patch, s_patch, r_patch], loc='upper left',
              facecolor='#1a1a2e', labelcolor='white', fontsize=9)

    fig.savefig(chart_path, dpi=110, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close('all')

    s_str = ", ".join([f"₹{v:.1f}" for _, v in supports]) if supports else "none found"
    r_str = ", ".join([f"₹{v:.1f}" for _, v in resistances]) if resistances else "none found"

    return {
        "success": (
            f"Support & Resistance chart for {symbol} has been generated and is attached below. "
            f"Key support levels: {s_str}. Key resistance levels: {r_str}. "
            f"Current price: ₹{current_price:,.2f}."
        )
    }