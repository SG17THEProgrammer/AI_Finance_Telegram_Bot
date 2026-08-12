"""
Live financial data, hybrid-sourced:
  - Finnhub: primary for US-listed stocks (real-time quotes, company news)
  - yfinance: Indian (NSE/BSE) stocks and general fallback (no key needed)
  - SEC EDGAR: official regulatory filings (US-listed companies only)

Hard rule: every function here returns an explicit "not found" style result
when data can't be retrieved. NOTHING in this module ever invents a number.
The LLM is instructed (system_prompt.py) to pass that uncertainty straight
through to the user rather than filling the gap with a guess.
"""

import logging
import httpx
import yfinance as yf
import os
import matplotlib
matplotlib.use('Agg') # CRITICAL: Prevents server crash on Linux
import matplotlib.pyplot as plt
import mplfinance as mpf

from app.config import FINNHUB_API_KEY

# yfinance logs a scary-looking traceback-style message internally every time
# a symbol isn't found, even though we handle that gracefully - silence it so
# normal "tried X, falling back to Y" lookups don't look like crashes.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

FINNHUB_BASE = "https://finnhub.io/api/v1"
SEC_HEADERS = {"User-Agent": "Atlas Finance Assistant (hackathon-project@example.com)"}

# Common company-name -> ticker aliases. The LLM sometimes passes a company
# name instead of its actual ticker (e.g. "Infosys" instead of "INFY") - our
# lookups are ticker-based, so without this, correctly-named-but-wrong-symbol
# queries silently fail even though the company is perfectly well covered.
# Not exhaustive - just the common ones likely to come up in testing/demo.
SYMBOL_ALIASES = {
    "INFOSYS": "INFY",
    "TATA CONSULTANCY SERVICES": "TCS",
    "RELIANCE INDUSTRIES": "RELIANCE",
    "ICICI BANK": "ICICIBANK",
    "HDFC BANK": "HDFCBANK",
    "STATE BANK OF INDIA": "SBIN",
    "SBI": "SBIN",
    "TATA MOTORS": "TATAMOTORS",
    "WIPRO": "WIPRO",
    "BHARTI AIRTEL": "BHARTIARTL",
    "AIRTEL": "BHARTIARTL",
    "APPLE": "AAPL",
    "MICROSOFT": "MSFT",
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "AMAZON": "AMZN",
    "TESLA": "TSLA",
    "META": "META",
    "FACEBOOK": "META",
    "NVIDIA": "NVDA",
}


def _normalize_symbol(query: str) -> str:
    symbol = query.strip().upper()
    return SYMBOL_ALIASES.get(symbol, symbol)


_cik_map_cache = None  # lazy-loaded ticker -> CIK map for SEC EDGAR


def _finnhub_quote(symbol: str):
    if not FINNHUB_API_KEY:
        return None
    try:
        resp = httpx.get(
            f"{FINNHUB_BASE}/quote",
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
            timeout=8,
        )
        data = resp.json()
        # Finnhub returns all-zero fields for an unknown symbol rather than a 404
        if not data or data.get("c") in (None, 0):
            return None
        return {
            "symbol": symbol.upper(),
            "price": data["c"],
            "change": data.get("d"),
            "change_percent": data.get("dp"),
            "day_high": data.get("h"),
            "day_low": data.get("l"),
            "prev_close": data.get("pc"),
            "currency": "USD",
            "source": "Finnhub (real-time)",
        }
    except Exception:
        return None


def _yfinance_quote(symbol: str):
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = getattr(info, "last_price", None)
        if not price:
            return None
        prev_close = getattr(info, "previous_close", None)
        change = (price - prev_close) if prev_close else None
        change_percent = (change / prev_close * 100) if prev_close else None
        return {
            "symbol": symbol.upper(),
            "price": round(price, 2),
            "change": round(change, 2) if change is not None else None,
            "change_percent": round(change_percent, 2) if change_percent is not None else None,
            "day_high": getattr(info, "day_high", None),
            "day_low": getattr(info, "day_low", None),
            "prev_close": prev_close,
            "currency": getattr(info, "currency", "USD"),
            "source": "Yahoo Finance",
        }
    except Exception:
        return None


def get_stock_quote(query: str) -> dict:
    """
    Hybrid lookup. Tries Finnhub first (US), then common Indian exchange
    suffixes (.NS = NSE, .BO = BSE), then the bare symbol as a last resort
    (bare symbols risk colliding with unrelated/delisted US tickers, so they
    go last, not first). Returns a dict with an 'error' key if nothing is
    found anywhere - callers must surface that honestly, never guess a price.
    """
    symbol = _normalize_symbol(query)

    result = _finnhub_quote(symbol)
    if result:
        return result

    for candidate in [f"{symbol}.NS", f"{symbol}.BO", symbol]:
        result = _yfinance_quote(candidate)
        if result:
            return result

    return {
        "error": (
            f"No live quote found for '{query}' on Finnhub or Yahoo Finance "
            "(tried US listing and NSE/BSE). The ticker may be wrong, delisted, "
            "or not covered by either source."
        )
    }


def get_company_news(query: str, limit: int = 5) -> dict:
    """
    Recent company news headlines. Tries Finnhub (US) first, then yfinance.
    IMPORTANT: yfinance's news feed for Indian tickers often returns broader
    sector/related news, not headlines actually about the requested company.
    We filter by checking each article's relatedTickers so we can honestly
    tell the caller whether headlines are genuinely company-specific or just
    general sector context - this distinction gets passed to the LLM so it
    doesn't present loosely-related news as if it were direct TCS news.
    """
    symbol = _normalize_symbol(query)

    if FINNHUB_API_KEY:
        try:
            import datetime

            today = datetime.date.today()
            week_ago = today - datetime.timedelta(days=7)
            resp = httpx.get(
                f"{FINNHUB_BASE}/company-news",
                params={
                    "symbol": symbol,
                    "from": week_ago.isoformat(),
                    "to": today.isoformat(),
                    "token": FINNHUB_API_KEY,
                },
                timeout=8,
            )
            data = resp.json()
            if data:
                headlines = [
                    {"headline": item.get("headline"), "source": item.get("source"), "url": item.get("url")}
                    for item in data[:limit]
                ]
                # Finnhub's company-news endpoint is already ticker-scoped by the API
                # itself, so these are genuinely company-specific.
                return {
                    "symbol": symbol,
                    "headlines": headlines,
                    "source": "Finnhub",
                    "company_specific": True,
                }
        except Exception:
            pass

    try:
        for candidate in [f"{symbol}.NS", f"{symbol}.BO", symbol]:
            ticker = yf.Ticker(candidate)
            news = ticker.news
            if not news:
                continue

            base_symbol = candidate.split(".")[0].upper()
            specific, general = [], []

            for item in news[:20]:
                content = item.get("content", {})
                related = content.get("relatedTickers") or item.get("relatedTickers") or []
                related_upper = [str(r).upper() for r in related]

                headline_obj = {
                    "headline": content.get("title", item.get("title", "")),
                    "source": content.get("provider", {}).get("displayName", "Yahoo Finance"),
                    "url": content.get("canonicalUrl", {}).get("url", ""),
                }

                is_specific = any(base_symbol in r or candidate.upper() in r for r in related_upper)
                (specific if is_specific else general).append(headline_obj)

            if specific:
                return {
                    "symbol": symbol,
                    "headlines": specific[:limit],
                    "source": "Yahoo Finance",
                    "company_specific": True,
                }
            elif general:
                return {
                    "symbol": symbol,
                    "headlines": general[:limit],
                    "source": "Yahoo Finance",
                    "company_specific": False,
                    "note": (
                        f"No headlines found tagged directly to {symbol} - these are "
                        "broader sector/related-company articles, not confirmed to be "
                        "specifically about this company."
                    ),
                }
    except Exception:
        pass

    return {"error": f"No recent news found for '{query}' on Finnhub or Yahoo Finance."}


def get_company_fundamentals(query: str) -> dict:
    """
    Company fundamentals (market cap, sector, industry, employees, P/E, 52-week
    range). Tries Finnhub's company profile (US) first, then yfinance's info
    dict. Only returns fields the source actually provides - fields that
    aren't available are simply omitted, never invented. Revenue/net profit
    are deliberately NOT included here because reliable, consistently-available
    figures for both US and Indian tickers aren't guaranteed from these free
    sources - the LLM is instructed not to state those unless a tool actually
    returns them.
    """
    symbol = _normalize_symbol(query)

    if FINNHUB_API_KEY:
        try:
            resp = httpx.get(
                f"{FINNHUB_BASE}/stock/profile2",
                params={"symbol": symbol, "token": FINNHUB_API_KEY},
                timeout=8,
            )
            data = resp.json()
            if data and data.get("name"):
                return {
                    "symbol": symbol,
                    "name": data.get("name"),
                    "market_cap_musd": data.get("marketCapitalization"),
                    "industry": data.get("finnhubIndustry"),
                    "exchange": data.get("exchange"),
                    "ipo_date": data.get("ipo"),
                    "source": "Finnhub",
                }
        except Exception:
            pass

    try:
        for candidate in [f"{symbol}.NS", f"{symbol}.BO", symbol]:
            ticker = yf.Ticker(candidate)
            info = ticker.info
            if not info or not info.get("longName"):
                continue

            fields = {
                "symbol": symbol,
                "name": info.get("longName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "market_cap": info.get("marketCap"),
                "currency": info.get("currency"),
                "employees": info.get("fullTimeEmployees"),
                "pe_ratio": info.get("trailingPE"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "source": "Yahoo Finance",
            }
            # Drop any field the source didn't actually provide, rather than
            # passing along None values that might read as "confirmed zero/empty".
            return {k: v for k, v in fields.items() if v is not None}
    except Exception:
        pass

    return {"error": f"No fundamentals data found for '{query}'."}


def _load_cik_map():
    global _cik_map_cache
    if _cik_map_cache is not None:
        return _cik_map_cache
    try:
        resp = httpx.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_HEADERS,
            timeout=10,
        )
        data = resp.json()
        _cik_map_cache = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}
    except Exception:
        _cik_map_cache = {}
    return _cik_map_cache


def get_sec_filings(query: str, limit: int = 5) -> dict:
    """
    Recent SEC filings for a US-listed company. India has no equivalent free
    public API, so this is honestly scoped to US-listed tickers only - if the
    ticker isn't in SEC's own ticker map, we say so rather than guessing.
    """
    symbol = _normalize_symbol(query)
    cik_map = _load_cik_map()
    cik = cik_map.get(symbol)
    if not cik:
        return {
            "error": (
                f"'{query}' isn't in SEC EDGAR's ticker list - it's likely not a "
                "US-listed company. SEC filings are only available for US-listed "
                "companies (this data source has no coverage for Indian markets)."
            )
        }

    try:
        resp = httpx.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=SEC_HEADERS,
            timeout=10,
        )
        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accns = recent.get("accessionNumber", [])

        filings = []
        for i in range(min(limit, len(forms))):
            accn_nodash = accns[i].replace("-", "")
            filings.append(
                {
                    "form": forms[i],
                    "date": dates[i],
                    "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/{accns[i]}-index.htm",
                }
            )
        return {"symbol": symbol, "filings": filings, "source": "SEC EDGAR"}
    except Exception:
        return {"error": f"Could not retrieve SEC filings for '{query}' right now."}


def generate_stock_chart(query: str, telegram_id: str, chart_type: str = "line", period: str = "3mo") -> dict:
    """Fetches historical data and generates a line, candle, or bar chart image."""
    chart_path = f"chart_{telegram_id}.png"
    
    # 1. PURGE OLD STATE: Delete leftover images & clear RAM to prevent ghost charts!
    if os.path.exists(chart_path):
        try:
            os.remove(chart_path)
        except Exception:
            pass
    plt.close('all') 
    
    symbol = _normalize_symbol(query)
    
    # 2. Fetch data
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period)
    
    if hist.empty:
        # Fallback to Indian markets
        ticker = yf.Ticker(f"{symbol}.NS")
        hist = ticker.history(period=period)
        if hist.empty:
            plt.close('all') # Prevent memory leaks before returning
            return {"error": f"CRITICAL: I could not fetch '{period}' of historical data for {query}. The API might be rate-limited or the data is unavailable."}
        symbol = f"{symbol}.NS"
        
    # 3. Map the requested chart type to mplfinance syntax
    valid_types = {"line": "line", "candle": "candle", "bar": "ohlc"}
    plot_type = valid_types.get(chart_type.lower(), "line")
    
    # 4. Create a professional style (Green for up, Red for down)
    mc = mpf.make_marketcolors(up='g', down='r', inherit=True)
    s  = mpf.make_mpf_style(marketcolors=mc, gridstyle='--', gridaxis='both')
    
    # 5. Draw and save the chart
    mpf.plot(hist, type=plot_type, style=s,
             title=f"{symbol} - {period} History",
             ylabel='Price',
             savefig=dict(fname=chart_path, dpi=100, bbox_inches='tight'))
             
    # 6. Final RAM Cleanup
    plt.close('all') 
    
    return {"success": f"A {chart_type} chart for {symbol} over a '{period}' period has been generated and attached. Tell the user it is attached below."}



def generate_comparison_chart(queries: list, telegram_id: str, period: str = "6mo") -> dict:    
    chart_path = f"chart_{telegram_id}.png"
    
    # 1. PURGE OLD STATE
    if os.path.exists(chart_path):
        try:
            os.remove(chart_path)
        except Exception:
            pass
    plt.close('all') 
    
    plt.figure(figsize=(10, 5))
    valid_tickers = []
    failed_tickers = []
    
    for q in queries:
        symbol = _normalize_symbol(q)
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)
        
        # Fallback for Indian markets
        if hist.empty:
            symbol_ns = f"{symbol}.NS"
            ticker = yf.Ticker(symbol_ns)
            hist = ticker.history(period=period)
            if not hist.empty:
                symbol = symbol_ns
                
        if not hist.empty:
            first_price = hist['Close'].iloc[0]
            pct_change = ((hist['Close'] - first_price) / first_price) * 100
            
            plt.plot(hist.index, pct_change, label=symbol, linewidth=2)
            valid_tickers.append(symbol)
        else:
            failed_tickers.append(q)
            
    # 2. FAIL FAST: If ANY ticker fails, ABORT the entire chart.
    if failed_tickers:
        plt.close('all') # Clear RAM
        # We return a hard error. This forces the LLM's ReAct loop to either
        # self-correct the ticker (e.g. changing "Apple" to "AAPL") and try again,
        # or explicitly apologize to the user. NO partial charts!
        return {
            "error": (
                f"Fetch failed for: {', '.join(failed_tickers)}. Chart generation aborted. "
                "Ensure you are passing valid official stock tickers (e.g. 'AAPL' instead of 'Apple'). "
                "Correct the tickers and call this tool again. If the ticker is already correct, "
                "the data is unavailable and you must tell the user."
            )
        }
        
    # 3. If all successful, save and return
    plt.title(f"Relative Performance Comparison ({period})")
    plt.xlabel("Date")
    plt.ylabel("Growth (Percentage Change %)")
    plt.legend()
    plt.grid(True, linestyle='--', alpha=0.7)
    
    plt.savefig(chart_path, bbox_inches='tight')
    plt.close('all')
    
    return {"success": f"Chart generated successfully for {', '.join(valid_tickers)}. Tell user it is attached."}