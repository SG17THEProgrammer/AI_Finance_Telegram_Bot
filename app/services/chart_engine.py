"""
app/services/chart_engine.py

Analytically meaningful chart types for Atlas. Each function generates a
chart as chart_{telegram_id}.png and returns a success/error dict — same
pattern as financial_data.py so handlers.py picks them up automatically.

Charts:
  1. generate_candlestick_with_ma   — candles + 50-day & 200-day MA
  2. generate_rsi_gauge             — RSI panel + price panel
  3. generate_sector_heatmap        — NSE/global sector performance grid
  4. generate_fundamental_radar     — P/E, P/B, ROE vs industry avg radar
  5. generate_support_resistance    — price chart with auto S/R lines

FIX LOG:
  - All text forced white (color='white') — no more invisible labels
  - DPI raised to 150 for sharper images
  - Heatmap: gradient intensity via colormap, color scale bar, tighter layout
  - Index aliases added: NIFTY50/NIFTY 50 → ^NSEI, etc. so index charts work
  - Sector compare data available for LLM "today vs yesterday" queries
"""

import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # CRITICAL: prevents crash on Linux/Railway
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import matplotlib.colors as mcolors
import mplfinance as mpf
import yfinance as yf

from app.services.financial_data import _normalize_symbol


# ── Index aliases — identical to alert_engine.py so index charts work ─────────
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
    "NDX":        "^NDX",       # ← ADD THIS
    "QQQ":        "^NDX",       # ← ADD THIS — LLM sometimes passes ETF proxy
    "SP500":      "^GSPC",
    "S&P500":     "^GSPC",
    "S&P 500":    "^GSPC",
    "SPY":        "^GSPC",      # ← ADD THIS
    "IVV":        "^GSPC",      # ← ADD THIS
}

# Representative tickers for sector heatmap
_SECTOR_TICKERS = {
    "IT": "TCS.NS",
    "Banking": "HDFCBANK.NS",
    "Auto": "TATAMOTORS.NS",
    "Pharma": "SUNPHARMA.NS",
    "FMCG": "HINDUNILVR.NS",
    "Energy": "RELIANCE.NS",
    "Metals": "TATASTEEL.NS",
    "Infra": "LT.NS",
    "Nifty50": "^NSEI",
    "BankNifty": "^NSEBANK",
    "S&P500": "^GSPC",
    "Nasdaq": "^IXIC",
}

# Dark theme shared style constants
_BG = '#0d1117'
_PANEL = '#161b22'
_GRID = '#21262d'
_TEXT = '#e6edf3'
_ACCENT_BLUE = '#2196F3'
_ACCENT_ORANGE = '#FF9800'
_GREEN = '#26a69a'
_RED = '#ef5350'


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _purge_chart(chart_path: str):
    """Delete leftover chart file and close all matplotlib figures."""
    if os.path.exists(chart_path):
        try:
            os.remove(chart_path)
        except Exception:
            pass
    plt.close('all')


def _resolve_symbol(query: str) -> str:
    """
    Resolves index aliases first (NIFTY50 → ^NSEI), then falls back to
    financial_data's _normalize_symbol for stock tickers.
    This is why NIFTY50 candlestick charts were failing — _normalize_symbol
    has no index aliases, so ^NSEI was never tried.
    """
    upper = query.strip().upper()
    if upper in _INDEX_ALIASES:
        return _INDEX_ALIASES[upper]
    return _normalize_symbol(query)


def _fetch_history(symbol: str, period: str = "6mo") -> tuple[pd.DataFrame, str]:
    """
    Fetches OHLCV history. Returns (dataframe, resolved_symbol).
    For index symbols (^NSEI etc) tries bare symbol directly.
    For equity symbols tries NSE suffix fallback.
    """
    # Index symbols start with ^, try as-is first
    if symbol.startswith("^"):
        hist = yf.Ticker(symbol).history(period=period)
        return hist, symbol

    # Equity: try bare, then .NS, then .BO
    for candidate in [symbol, f"{symbol}.NS", f"{symbol}.BO"]:
        hist = yf.Ticker(candidate).history(period=period)
        if not hist.empty:
            return hist, candidate

    return pd.DataFrame(), symbol


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI — same method as alert_engine.py."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _style_axes(ax, title: str = ""):
    """Apply consistent dark-theme styling to any standard (non-polar) axis."""
    ax.set_facecolor(_PANEL)
    ax.tick_params(colors=_TEXT, labelsize=9)
    ax.xaxis.label.set_color(_TEXT)
    ax.yaxis.label.set_color(_TEXT)
    for spine in ax.spines.values():
        spine.set_edgecolor(_GRID)
    ax.grid(True, linestyle='--', alpha=0.3, color=_GRID)
    if title:
        ax.set_title(title, color=_TEXT, fontsize=12, fontweight='bold', pad=10)


# ── 1. Candlestick + 50-day & 200-day Moving Averages ─────────────────────────

def generate_candlestick_with_ma(query: str, telegram_id: str, period: str = "6mo") -> dict:
    """
    Professional candlestick chart overlaid with 50-day and 200-day moving
    averages. Works for both stocks (TCS, RELIANCE) and indices (NIFTY50, ^NSEI).
    The MA crossover (Golden Cross / Death Cross) is the most-watched institutional signal.
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    symbol = _resolve_symbol(query)

    # Need 1y of data to compute 200-day MA even if user wants a 6mo view
    hist, resolved = _fetch_history(symbol, period="1y")

    if hist.empty:
        return {"error": f"Could not fetch price history for '{query}'. Ticker may be unavailable or the market is closed."}

    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    hist['MA50'] = hist['Close'].rolling(window=50).mean()
    hist['MA200'] = hist['Close'].rolling(window=200).mean()

    # Slice to user-requested display period
    period_map = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252, "2y": 504}
    display_rows = period_map.get(period, 126)
    hist_display = hist.tail(display_rows).copy()

    ap = []
    if hist_display['MA50'].notna().any():
        ap.append(mpf.make_addplot(hist_display['MA50'], color=_ACCENT_BLUE,
                                   width=1.8, label='MA50'))
    if hist_display['MA200'].notna().any():
        ap.append(mpf.make_addplot(hist_display['MA200'], color=_ACCENT_ORANGE,
                                   width=1.8, label='MA200'))

    mc = mpf.make_marketcolors(up=_GREEN, down=_RED, inherit=True)
    s = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle='--',
        gridaxis='both',
        facecolor=_PANEL,
        figcolor=_BG,
        rc={
            'axes.labelcolor': _TEXT,
            'xtick.color': _TEXT,
            'ytick.color': _TEXT,
            'axes.titlecolor': _TEXT,
            'text.color': _TEXT,
        }
    )

    display_name = query.upper() if query.upper() in _INDEX_ALIASES else resolved.split(".")[0]
    fig, axes = mpf.plot(
        hist_display,
        type='candle',
        style=s,
        title=f"\n{display_name} — Candlestick + MA50 & MA200 ({period})",
        ylabel='Price',
        ylabel_lower='Volume',
        addplot=ap if ap else None,
        volume=True,
        returnfig=True,
        figsize=(13, 7),
    )

    # Force all text white — mplfinance sometimes overrides our rc settings
    for ax in fig.get_axes():
        ax.title.set_color(_TEXT)
        ax.xaxis.label.set_color(_TEXT)
        ax.yaxis.label.set_color(_TEXT)
        ax.tick_params(colors=_TEXT)
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_color(_TEXT)

    legend_patches = []
    if hist_display['MA50'].notna().any():
        legend_patches.append(mpatches.Patch(color=_ACCENT_BLUE, label='50-day MA'))
    if hist_display['MA200'].notna().any():
        legend_patches.append(mpatches.Patch(color=_ACCENT_ORANGE, label='200-day MA'))
    if legend_patches:
        axes[0].legend(handles=legend_patches, loc='upper left',
                       facecolor=_PANEL, labelcolor=_TEXT, fontsize=9,
                       edgecolor=_GRID)

    # Annotate current MA values
    last_ma50 = hist_display['MA50'].dropna().iloc[-1] if hist_display['MA50'].notna().any() else None
    last_ma200 = hist_display['MA200'].dropna().iloc[-1] if hist_display['MA200'].notna().any() else None
    last_close = float(hist_display['Close'].iloc[-1])

    fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=_BG)
    plt.close('all')

    # Build meaningful summary for LLM
    trend_signal = ""
    if last_ma50 and last_ma200:
        if last_ma50 > last_ma200:
            trend_signal = "MA50 is ABOVE MA200 → Bullish trend (Golden Cross territory)."
        else:
            trend_signal = "MA50 is BELOW MA200 → Bearish trend (Death Cross territory)."

    return {
        "success": (
            f"Candlestick chart with MA50 & MA200 for {display_name} ({period}) is attached below. "
            f"Current price: {last_close:,.2f}. "
            + (f"MA50: {last_ma50:,.2f}. MA200: {last_ma200:,.2f}. " if last_ma50 and last_ma200 else "")
            + trend_signal
        )
    }


# ── 2. RSI Gauge + Price Panel ─────────────────────────────────────────────────

def generate_rsi_gauge(query: str, telegram_id: str, period: str = "3mo") -> dict:
    """
    Two-panel: top = RSI-14 line with overbought/oversold bands shaded,
    bottom = price line for context. Works for stocks and indices.
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    symbol = _resolve_symbol(query)
    hist, resolved = _fetch_history(symbol, period=period)

    if hist.empty:
        return {"error": f"Could not fetch price history for '{query}'."}

    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    close = hist['Close'].dropna()
    rsi = _compute_rsi(close).dropna()

    if rsi.empty:
        return {"error": f"Not enough history to compute RSI for '{query}' (need at least 14 days)."}

    current_rsi = round(float(rsi.iloc[-1]), 1)
    display_name = query.upper() if query.upper() in _INDEX_ALIASES else resolved.split(".")[0]

    if current_rsi < 30:
        zone = "OVERSOLD 🟢 — Potential buying opportunity"
        rsi_color = _GREEN
    elif current_rsi > 70:
        zone = "OVERBOUGHT 🔴 — Potential caution / selling zone"
        rsi_color = _RED
    else:
        zone = "NEUTRAL ⚪ — No extreme RSI signal"
        rsi_color = '#FFC107'

    fig = plt.figure(figsize=(13, 8), facecolor=_BG)
    gs = gridspec.GridSpec(2, 1, height_ratios=[1, 1.5], hspace=0.4, figure=fig)

    # ── RSI panel ──
    ax_rsi = fig.add_subplot(gs[0])
    _style_axes(ax_rsi)
    ax_rsi.plot(rsi.index, rsi.values, color=rsi_color, linewidth=2, label='RSI-14')
    ax_rsi.axhline(70, color=_RED, linestyle='--', linewidth=1.2, alpha=0.9, label='Overbought (70)')
    ax_rsi.axhline(30, color=_GREEN, linestyle='--', linewidth=1.2, alpha=0.9, label='Oversold (30)')
    ax_rsi.fill_between(rsi.index, rsi.values, 70,
                        where=(rsi.values > 70), alpha=0.2, color=_RED)
    ax_rsi.fill_between(rsi.index, rsi.values, 30,
                        where=(rsi.values < 30), alpha=0.2, color=_GREEN)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel('RSI-14', color=_TEXT, fontsize=10)
    ax_rsi.set_title(
        f'{display_name}  |  RSI: {current_rsi}  |  {zone}',
        color=_TEXT, fontsize=11, fontweight='bold', pad=10
    )
    ax_rsi.legend(loc='upper left', facecolor=_PANEL, labelcolor=_TEXT,
                  fontsize=8, edgecolor=_GRID)
    ax_rsi.tick_params(colors=_TEXT)

    # ── Price panel ──
    ax_price = fig.add_subplot(gs[1])
    _style_axes(ax_price, title=f'{display_name} — Price ({period})')
    ax_price.plot(close.index, close.values, color='#90CAF9', linewidth=1.8)
    ax_price.fill_between(close.index, close.values, close.values.min(),
                          alpha=0.08, color='#90CAF9')
    ax_price.set_ylabel('Price', color=_TEXT, fontsize=10)
    ax_price.set_xlabel('Date', color=_TEXT, fontsize=9)
    ax_price.tick_params(colors=_TEXT)

    fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=_BG)
    plt.close('all')

    return {
        "success": (
            f"RSI gauge chart for {display_name} is attached below. "
            f"Current RSI-14: {current_rsi} — {zone}."
        )
    }


# ── 3. Sector Heatmap — improved UI ───────────────────────────────────────────

def generate_sector_heatmap(telegram_id: str) -> dict:
    """
    Professional color-gradient heatmap of Indian sectors + global indices.
    Uses a proper colormap (RdYlGn) with a color scale bar — similar to
    Bloomberg/TradingView heatmaps. Green = up, Red = down, intensity = magnitude.
    Also returns raw data so the LLM can answer "compare today vs yesterday".
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    perf_today = {}
    perf_yesterday = {}

    for sector, ticker in _SECTOR_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            hist = hist[hist['Volume'] > 0]  # filter non-trading days

            if len(hist) >= 2:
                prev_prev = float(hist['Close'].iloc[-2])
                prev = float(hist['Close'].iloc[-1])
                perf_today[sector] = round(((prev - prev_prev) / prev_prev) * 100, 2)

            if len(hist) >= 3:
                pp = float(hist['Close'].iloc[-3])
                p = float(hist['Close'].iloc[-2])
                perf_yesterday[sector] = round(((p - pp) / pp) * 100, 2)
        except Exception:
            pass

    if not perf_today:
        return {"error": "Could not fetch sector data. Market data APIs may be rate-limited."}

    labels = list(perf_today.keys())
    values = np.array([perf_today[k] for k in labels], dtype=float)

    # Layout: 4 columns
    cols = 4
    rows = int(np.ceil(len(labels) / cols))

    fig_w = cols * 3.2
    fig_h = rows * 2.4 + 0.8  # extra for title + colorbar

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=_BG)

    # Reserve space: main grid + colorbar on the right
    gs = gridspec.GridSpec(
        rows, cols + 1,
        width_ratios=[1] * cols + [0.08],
        hspace=0.08,
        wspace=0.08,
        figure=fig,
        left=0.03, right=0.95, top=0.88, bottom=0.04
    )

    # Colormap: red → yellow → green, symmetric around 0
    cmap = matplotlib.colormaps['RdYlGn']
    max_abs = max(abs(values).max(), 0.5)  # at least 0.5% range so map isn't flat
    norm = mcolors.TwoSlopeNorm(vmin=-max_abs, vcenter=0, vmax=max_abs)

    for i, (label, val) in enumerate(zip(labels, values)):
        row_i, col_i = divmod(i, cols)
        ax = fig.add_subplot(gs[row_i, col_i])
        ax.set_facecolor(_BG)
        ax.axis('off')

        bg = _tile_bg(val, max_abs)

        # Base rounded tile
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.03, 0.04), 0.94, 0.92,
            boxstyle="round,pad=0.04", lw=0,
            facecolor=bg, transform=ax.transAxes, zorder=1, clip_on=False))

        # Gloss strip
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.03, 0.76), 0.94, 0.20,
            boxstyle="round,pad=0.03", lw=0,
            facecolor='white', alpha=0.08,
            transform=ax.transAxes, zorder=2, clip_on=False))

        # Sector name
        ax.text(0.5, 0.62, label,
                transform=ax.transAxes, ha='center', va='center',
                color='white', fontsize=10.5, fontweight='bold', zorder=3)

        # Percentage
        sign = "+" if val >= 0 else ""
        ax.text(0.5, 0.30, f"{sign}{val:.2f}%",
                transform=ax.transAxes, ha='center', va='center',
                color='white', fontsize=16, fontweight='bold', zorder=3)

        # Percentage — larger, bold
        sign = "+" if val >= 0 else ""
        ax.text(0.5, 0.30, f"{sign}{val:.2f}%",
                transform=ax.transAxes,
                ha='center', va='center',
                color='white', fontsize=13, fontweight='bold', zorder=3)

    # Fill any empty cells in the last row with blank panels
    total_cells = rows * cols
    for i in range(len(labels), total_cells):
        row_i, col_i = divmod(i, cols)
        ax = fig.add_subplot(gs[row_i, col_i])
        ax.set_facecolor(_BG)
        ax.axis('off')

    # Color scale bar
    cbar_ax = fig.add_subplot(gs[:, cols])
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, cax=cbar_ax)
    cbar.ax.tick_params(colors=_TEXT, labelsize=8)
    cbar.set_label('Return %', color=_TEXT, fontsize=9)
    cbar.outline.set_edgecolor(_GRID)

    fig.suptitle('📊  Market Sector Heatmap — Today\'s Performance',
                 color=_TEXT, fontsize=14, fontweight='bold', y=0.97)

    fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=_BG)
    plt.close('all')

    top_gainer = max(perf_today, key=perf_today.get)
    top_loser = min(perf_today, key=perf_today.get)

    # Build comparison text if we have yesterday's data
    compare_lines = []
    if perf_yesterday:
        for sector in labels:
            if sector in perf_yesterday:
                delta = perf_today[sector] - perf_yesterday[sector]
                compare_lines.append(
                    f"{sector}: today {perf_today[sector]:+.2f}% vs yesterday {perf_yesterday[sector]:+.2f}% (Δ{delta:+.2f}%)"
                )

    return {
        "success": (
            f"Sector heatmap is attached below. "
            f"Best: {top_gainer} ({perf_today[top_gainer]:+.2f}%). "
            f"Worst: {top_loser} ({perf_today[top_loser]:+.2f}%)."
        ),
        "today": perf_today,
        "yesterday": perf_yesterday,
        "comparison": compare_lines,
    }


# ── 4. Fundamental Radar Chart ────────────────────────────────────────────────

def generate_fundamental_radar(query: str, telegram_id: str) -> dict:
    """
    Radar (spider) chart: P/E, P/B, ROE, Profit Margin, Revenue Growth
    vs approximate Indian market averages. Shows valuation positioning visually.
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    symbol = _resolve_symbol(query)
    info = None

    for candidate in [f"{symbol}.NS", f"{symbol}.BO", symbol]:
        try:
            t = yf.Ticker(candidate)
            i = t.info
            if i and i.get("longName"):
                info = i
                break
        except Exception:
            continue

    if not info or not info.get("longName"):
        return {"error": f"Could not fetch fundamental data for '{query}'. Only equities are supported for this chart."}

    # (stock_value, market_average)
    metrics = {
        "P/E\nRatio":        (info.get("trailingPE"),                       22.0),
        "P/B\nRatio":        (info.get("priceToBook"),                       3.5),
        "ROE\n(%)":          ((info.get("returnOnEquity") or 0) * 100,       15.0),
        "Profit\nMargin(%)": ((info.get("profitMargins") or 0) * 100,        12.0),
        "Rev Growth\n(%)":   ((info.get("revenueGrowth") or 0) * 100,        10.0),
    }

    labels = list(metrics.keys())
    stock_raw = [v[0] if v[0] is not None else 0 for v in metrics.values()]
    avg_raw   = [v[1] for v in metrics.values()]

    def _norm(val, avg):
        scale = avg * 2 if avg != 0 else 1
        return min(max((val / scale) * 100, 0), 100)

    stock_norm = [_norm(sv, av) for sv, av in zip(stock_raw, avg_raw)]
    avg_norm   = [50.0] * len(labels)   # avg always = 50 after normalization

    N = len(labels)
    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    angles += angles[:1]

    stock_plot = stock_norm + stock_norm[:1]
    avg_plot   = avg_norm   + avg_norm[:1]

    fig, ax = plt.subplots(figsize=(9, 9), subplot_kw=dict(polar=True), facecolor=_BG)
    ax.set_facecolor(_PANEL)

    ax.plot(angles, stock_plot, 'o-', linewidth=2.2, color=_ACCENT_BLUE, label=symbol)
    ax.fill(angles, stock_plot, alpha=0.2, color=_ACCENT_BLUE)

    ax.plot(angles, avg_plot, 'o--', linewidth=1.8, color='#FFC107', label='Market Avg')
    ax.fill(angles, avg_plot, alpha=0.08, color='#FFC107')

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, color=_TEXT, fontsize=9.5)
    ax.set_yticklabels([])
    ax.set_ylim(0, 100)
    ax.grid(color=_GRID, linestyle='--', alpha=0.5)
    ax.spines['polar'].set_color(_GRID)

    company_name = info.get("longName", symbol)
    ax.set_title(f"{company_name}\nFundamentals vs Market Average",
                 color=_TEXT, fontsize=12, fontweight='bold', pad=20)
    ax.legend(loc='upper right', bbox_to_anchor=(1.35, 1.15),
              facecolor=_PANEL, labelcolor=_TEXT, fontsize=10, edgecolor=_GRID)

    # Actual values annotation at the bottom
    actuals = []
    for label, (sv, av) in zip(labels, metrics.values()):
        clean = label.replace('\n', ' ')
        sv_str = f"{sv:.1f}" if sv else "N/A"
        cmp = "▲" if (sv or 0) > av else "▼"
        actuals.append(f"{clean}: {sv_str} {cmp} (avg {av})")
    fig.text(0.5, 0.01, "   |   ".join(actuals),
             ha='center', color='#8b949e', fontsize=7.5, style='italic')

    fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=_BG)
    plt.close('all')

    summary_lines = []
    for label, (sv, av) in zip(labels, metrics.values()):
        clean = label.replace('\n', ' ')
        sv_str = f"{sv:.1f}" if sv else "N/A"
        comp = "above" if (sv or 0) > av else "below"
        summary_lines.append(f"{clean}: {sv_str} ({comp} market avg {av})")

    return {
        "success": (
            f"Fundamental radar chart for {company_name} is attached below. "
            + " | ".join(summary_lines)
        )
    }


# ── 5. Support / Resistance Line Chart ────────────────────────────────────────

def generate_support_resistance(query: str, telegram_id: str, period: str = "6mo") -> dict:
    """
    Price line chart with auto-detected horizontal support and resistance levels.
    Levels found via local price extremes; deduplicated within 1.5% bands.
    Works for stocks and indices.
    """
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    symbol = _resolve_symbol(query)
    hist, resolved = _fetch_history(symbol, period=period)

    if hist.empty:
        return {"error": f"Could not fetch price history for '{query}'."}

    if hist.index.tz is not None:
        hist.index = hist.index.tz_localize(None)

    close = hist['Close'].dropna()
    display_name = query.upper() if query.upper() in _INDEX_ALIASES else resolved.split(".")[0]

    # ── Detect local extremes ──
    window = max(5, len(close) // 20)
    levels = []
    vals = close.values

    for i in range(window, len(vals) - window):
        neighborhood = vals[i - window: i + window + 1]
        v = vals[i]
        if v == neighborhood.max():
            levels.append(('resistance', float(v)))
        elif v == neighborhood.min():
            levels.append(('support', float(v)))

    # Deduplicate: merge levels within 1.5% of each other
    def _cluster(raw, threshold=0.015):
        clustered = []
        for kind, val in sorted(raw, key=lambda x: x[1]):
            merged = False
            for j, (ck, cv) in enumerate(clustered):
                if abs(val - cv) / max(cv, 1e-9) < threshold:
                    clustered[j] = (ck, (cv + val) / 2)
                    merged = True
                    break
            if not merged:
                clustered.append((kind, val))
        return clustered

    levels = _cluster(levels)
    current = float(close.iloc[-1])

    supports = sorted(
        [(k, v) for k, v in levels if k == 'support' and v < current],
        key=lambda x: abs(x[1] - current)
    )[:3]
    resistances = sorted(
        [(k, v) for k, v in levels if k == 'resistance' and v > current],
        key=lambda x: abs(x[1] - current)
    )[:3]

    fig, ax = plt.subplots(figsize=(13, 6), facecolor=_BG)
    _style_axes(ax, title=f"{display_name} — Auto Support & Resistance ({period})")
    ax.set_facecolor(_PANEL)

    ax.plot(close.index, close.values, color='#90CAF9', linewidth=2, label='Price', zorder=3)
    ax.fill_between(close.index, close.values, close.values.min(),
                    alpha=0.07, color='#90CAF9')

    for _, val in supports:
        ax.axhline(val, color=_GREEN, linestyle='--', linewidth=1.3, alpha=0.9)
        ax.text(close.index[-1], val, f"  S  {val:,.1f}",
                color=_GREEN, fontsize=8.5, va='center', fontweight='bold')

    for _, val in resistances:
        ax.axhline(val, color=_RED, linestyle='--', linewidth=1.3, alpha=0.9)
        ax.text(close.index[-1], val, f"  R  {val:,.1f}",
                color=_RED, fontsize=8.5, va='center', fontweight='bold')

    ax.axhline(current, color='#FFC107', linestyle='-', linewidth=1, alpha=0.7)
    ax.text(close.index[int(len(close) * 0.02)], current,
            f"  Now: {current:,.1f}", color='#FFC107', fontsize=8.5, va='bottom')

    ax.set_ylabel('Price', color=_TEXT, fontsize=10)
    ax.set_xlabel('Date', color=_TEXT, fontsize=9)
    ax.tick_params(colors=_TEXT)

    patches = [
        mpatches.Patch(color='#90CAF9', label='Price'),
        mpatches.Patch(color=_GREEN, label='Support'),
        mpatches.Patch(color=_RED, label='Resistance'),
        mpatches.Patch(color='#FFC107', label='Current'),
    ]
    ax.legend(handles=patches, loc='upper left',
              facecolor=_PANEL, labelcolor=_TEXT, fontsize=9, edgecolor=_GRID)

    fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor=_BG)
    plt.close('all')

    s_str = ", ".join([f"{v:,.1f}" for _, v in supports]) if supports else "none detected"
    r_str = ", ".join([f"{v:,.1f}" for _, v in resistances]) if resistances else "none detected"

    return {
        "success": (
            f"Support & Resistance chart for {display_name} is attached below. "
            f"Current price: {current:,.2f}. "
            f"Key support levels: {s_str}. "
            f"Key resistance levels: {r_str}."
        )
    }

# ── 6. US Sector Heatmap ──────────────────────────────────────────────────────

_US_SECTOR_TICKERS = {
    "Technology":   "XLK",
    "Healthcare":   "XLV",
    "Financials":   "XLF",
    "Energy":       "XLE",
    "Industrials":  "XLI",
    "Cons Disc":    "XLY",
    "Cons Staples": "XLP",
    "Utilities":    "XLU",
    "Materials":    "XLB",
    "Real Estate":  "XLRE",
    "Comm Svcs":    "XLC",
    "S&P 500":      "^GSPC",
    "Nasdaq 100":   "^NDX",
    "Dow Jones":    "^DJI",
}


def _tile_bg(val: float, max_abs: float) -> str:
    """
    Returns a hex background color for a heatmap tile.
    Positive = green family, negative = red family.
    Intensity scales with magnitude relative to max_abs.
    """
    t = min(abs(val) / max(max_abs, 0.01), 1.0)
    if val >= 0:
        r = int(np.interp(t, [0, 1], [24,   0]))
        g = int(np.interp(t, [0, 1], [80, 190]))
        b = int(np.interp(t, [0, 1], [38,  70]))
    else:
        r = int(np.interp(t, [0, 1], [85, 200]))
        g = int(np.interp(t, [0, 1], [22,  40]))
        b = int(np.interp(t, [0, 1], [22,  40]))
    return f"#{r:02x}{g:02x}{b:02x}"


def generate_us_sector_heatmap(telegram_id: str) -> dict:
    """
    US sector heatmap using SPDR sector ETFs (XLK, XLV, XLF, etc.).
    Dark theme, rounded tiles, vivid saturation, ETF ticker labels.
    Also returns raw data so LLM can answer 'today vs yesterday' queries.
    """
    from datetime import datetime as _dt
    chart_path = f"chart_{telegram_id}.png"
    _purge_chart(chart_path)

    perf_today = {}
    perf_yesterday = {}

    for sector, ticker in _US_SECTOR_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            hist = hist[hist['Volume'] > 0]
            if len(hist) >= 2:
                prev     = float(hist['Close'].iloc[-1])
                prev_prev = float(hist['Close'].iloc[-2])
                perf_today[sector] = round(((prev - prev_prev) / prev_prev) * 100, 2)
            if len(hist) >= 3:
                p  = float(hist['Close'].iloc[-2])
                pp = float(hist['Close'].iloc[-3])
                perf_yesterday[sector] = round(((p - pp) / pp) * 100, 2)
        except Exception:
            pass

    if not perf_today:
        return {"error": "Could not fetch US sector data. Market may be closed or APIs rate-limited."}

    labels   = list(perf_today.keys())
    values   = [perf_today[k] for k in labels]
    max_abs  = max(abs(v) for v in values) or 1.0

    COLS  = 4
    ROWS  = int(np.ceil(len(labels) / COLS))
    FIG_W = 14.0
    FIG_H = ROWS * 2.45 + 1.35

    fig = plt.figure(figsize=(FIG_W, FIG_H), facecolor=_BG, dpi=160)

    # Title
    fig.text(0.5, 1 - 0.30 / FIG_H,
             "US Market Sector Heatmap",
             ha='center', va='top', color='#ffffff',
             fontsize=17, fontweight='bold')
    fig.text(0.5, 1 - 0.62 / FIG_H,
             f"Today's Performance  \u2022  SPDR Sector ETFs  \u2022  {_dt.now().strftime('%b %d, %Y')}",
             ha='center', va='top', color='#6e7681', fontsize=9)

    gs = gridspec.GridSpec(
        ROWS, COLS,
        left=0.012, right=0.988,
        top=1 - 0.90 / FIG_H,
        bottom=0.52 / FIG_H,
        hspace=0.09, wspace=0.055,
        figure=fig,
    )

    for i, (label, val) in enumerate(zip(labels, values)):
        r_i, c_i = divmod(i, COLS)
        ax = fig.add_subplot(gs[r_i, c_i])
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.axis('off'); ax.set_facecolor(_BG)

        bg = _tile_bg(val, max_abs)

        # Base rounded tile
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.03, 0.04), 0.94, 0.92,
            boxstyle="round,pad=0.04", lw=0,
            facecolor=bg, transform=ax.transAxes, zorder=1, clip_on=False))

        # Gloss strip at top of tile
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.03, 0.76), 0.94, 0.20,
            boxstyle="round,pad=0.03", lw=0,
            facecolor='white', alpha=0.08,
            transform=ax.transAxes, zorder=2, clip_on=False))

        # ETF ticker — small, top
        ticker = _US_SECTOR_TICKERS.get(label, "")
        if not ticker.startswith("^"):
            ax.text(0.5, 0.81, ticker,
                    transform=ax.transAxes, ha='center', va='center',
                    color='white', fontsize=8.5, alpha=0.80, zorder=4)

        # Sector name — middle
        ax.text(0.5, 0.58, label,
                transform=ax.transAxes, ha='center', va='center',
                color='white', fontsize=11.5, fontweight='bold', zorder=4)

        # Percentage — large, bottom
        sign = "+" if val >= 0 else ""
        ax.text(0.5, 0.27, f"{sign}{val:.2f}%",
                transform=ax.transAxes, ha='center', va='center',
                color='white', fontsize=18, fontweight='bold', zorder=4)

    # Empty cells
    for i in range(len(labels), ROWS * COLS):
        r_i, c_i = divmod(i, COLS)
        ax = fig.add_subplot(gs[r_i, c_i])
        ax.axis('off'); ax.set_facecolor(_BG)

    # Legend gradient bar
    bar_ax = fig.add_axes([0.18, 0.38 / FIG_H, 0.64, 0.014])
    grad = np.linspace(0, 1, 256).reshape(1, -1)
    bar_ax.imshow(grad, aspect='auto', cmap='RdYlGn', origin='lower')
    bar_ax.set_yticks([])
    bar_ax.set_xticks([0, 128, 255])
    bar_ax.set_xticklabels(['Underperforming', 'Flat', 'Outperforming'],
                            color='#6e7681', fontsize=8)
    bar_ax.tick_params(length=0)
    for sp in bar_ax.spines.values():
        sp.set_visible(False)

    fig.savefig(chart_path, dpi=160, bbox_inches='tight', facecolor=_BG)
    plt.close('all')

    top_gainer = max(perf_today, key=perf_today.get)
    top_loser  = min(perf_today, key=perf_today.get)

    compare_lines = []
    if perf_yesterday:
        for sector in labels:
            if sector in perf_yesterday:
                delta = perf_today[sector] - perf_yesterday[sector]
                compare_lines.append(
                    f"{sector}: today {perf_today[sector]:+.2f}% vs yesterday "
                    f"{perf_yesterday[sector]:+.2f}% (\u0394{delta:+.2f}%)"
                )

    return {
        "success": (
            f"US sector heatmap is attached below. "
            f"Best: {top_gainer} ({perf_today[top_gainer]:+.2f}%). "
            f"Worst: {top_loser} ({perf_today[top_loser]:+.2f}%)."
        ),
        "today": perf_today,
        "yesterday": perf_yesterday,
        "comparison": compare_lines,
    }