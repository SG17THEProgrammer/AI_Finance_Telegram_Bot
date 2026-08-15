"""
Tool (function-calling) definitions for the LLM. Phase 2 adds save_user_profile
and reset_conversation. Phase 3 adds live financial data tools.
"""

import json

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_user_profile",
            "description": (
                "Save or update something you've learned about the user during natural "
                "conversation - their role, sectors/companies they follow, stocks to watch, "
                "or preferred daily briefing time. Call this quietly in the background "
                "whenever the user shares this info, even in passing. Do not call this for "
                "information they haven't actually told you - never guess or fabricate values."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "User's role, e.g. Investor, Analyst, Founder, Student, Finance Professional",
                    },
                    "sectors": {
                        "type": "string",
                        "description": "Comma-separated sectors/industries/markets the user follows",
                    },
                    "watchlist": {
                        "type": "string",
                        "description": "Comma-separated stock tickers or company names the user wants monitored",
                    },
                    "briefing_time": {
                        "type": "string",
                        "description": "Preferred daily briefing time, in 24-hour HH:MM format, e.g. '08:00' or '18:30'. Convert whatever the user says (e.g. '8am', '6:30 in the evening') into this exact format before saving - this is required for scheduling to work correctly.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_conversation",
            "description": (
                "Permanently wipe this user's conversation history and learned profile "
                "(role, sectors, watchlist, briefing time). Call this ONLY when the user "
                "clearly and intentionally asks to clear/reset/forget their history or start "
                "fresh - never call this speculatively or as a joke response."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": (
                "Get a live/current stock price quote for a company. Works for both US-listed "
                "(e.g. AAPL, TSLA) and Indian NSE/BSE-listed (e.g. TCS, ICICIBANK, RELIANCE) "
                "stocks. ALWAYS call this when the user asks about a current price, how a stock "
                "is doing, or anything requiring a real-time number - never state a price from "
                "memory. If this returns an error, tell the user honestly that live data wasn't "
                "found rather than guessing a number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Stock ticker symbol preferred (e.g. 'TSLA', 'INFY', 'TCS'). Company name is also accepted as a fallback, but the correct ticker is more reliable.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_company_news",
            "description": (
                "Get recent real news headlines for a company (last 7 days). Call this when "
                "the user asks what's happening with a company, recent news, or why a stock "
                "moved. Never invent a news event - only report what this tool actually returns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Stock ticker symbol preferred (e.g. 'INFY' not 'Infosys'). Company name accepted as fallback.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sec_filings",
            "description": (
                "Get recent official SEC regulatory filings for a US-listed company (10-K, "
                "10-Q, 8-K, etc.). Only covers US-listed companies - if the tool returns an "
                "error for a non-US company (e.g. an Indian stock), tell the user this data "
                "source doesn't cover that market rather than guessing filing info."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "US stock ticker, e.g. 'AAPL', 'MSFT'",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_company_fundamentals",
            "description": (
                "Get company fundamentals: market cap, sector, industry, employee count, "
                "P/E ratio, 52-week range. Call this whenever the user asks about market cap, "
                "company size, sector/industry, employee count, or valuation. This tool does "
                "NOT provide revenue or net profit figures - if asked for those and this tool "
                "doesn't return them, say that data isn't available from your current sources "
                "rather than stating a number. Only report fields actually present in the "
                "tool's response - never fill in a metric it didn't return."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Stock ticker symbol preferred (e.g. 'INFY' not 'Infosys'). Company name accepted as fallback.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": (
                "Perform exact arithmetic. You MUST call this for ANY calculation involving more "
                "than one number - summing multiple line items into a total, computing a "
                "difference, a percentage change, anything. NEVER compute a sum or calculation "
                "yourself and state the result directly - even simple-looking addition of a few "
                "numbers must go through this tool, since mental arithmetic can silently be "
                "wrong even when every individual number you used was correct. This applies "
                "especially to financial figures (e.g. adding balance sheet line items into a "
                "total) where a wrong computed number is a serious error."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A simple arithmetic expression, e.g. '1998 + 10954 + 95088' or '40760 - 29965'. Only +, -, *, /, and parentheses are supported.",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "connect_google_sheets",
            "description": (
                "Call this when the user wants to connect their Google account for Sheets "
                "access (e.g. 'connect my google sheet', 'link my sheets'). Returns a real "
                "authorization link - share it with the user exactly as returned, formatted "
                "as a normal clickable link in your reply. Do not call this if the user is "
                "already connected (check profile info) unless they explicitly ask to "
                "reconnect."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_google_sheet",
            "description": (
                "Read data from a Google Sheet the user has pasted a link to. Only works if "
                "the user has already connected their Google account (check profile info) - "
                "if not connected, tell them to connect first rather than calling this. "
                "Returns the actual cell values - analyze/answer based only on this real data, "
                "never invent rows or figures not present in the returned values. Use the "
                "calculate tool for any sums/totals derived from the sheet data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sheet_url": {
                        "type": "string",
                        "description": "The Google Sheets URL (or bare sheet ID) the user shared",
                    }
                },
                "required": ["sheet_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_stock_chart",
            "description": "Generates a visual price history chart for a single stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g. 'AAPL', 'TCS')."
                    },
                    "chart_type": {
                        "type": "string",
                        "description": "Chart type. Must be one of: 'line', 'candle', 'bar'.",
                        "enum": ["line", "candle", "bar"]
                    },
                    "period": {
                        "type": "string",
                        "description": "The timeframe for the chart. Map the user's request to the closest valid option. Default is '3mo'.",
                        "enum": ["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_comparison_chart",
            "description": "Generates a single line chart comparing the percentage growth of MULTIPLE stocks. Call this when the user asks to compare two or more companies visually.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of stock tickers to compare (e.g. ['AAPL', 'MSFT', 'NVDA'])."
                    },
                    "period": {
                        "type": "string",
                        "description": "Timeframe for comparison. Default to '6mo'.",
                        "enum": ["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]
                    }
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_candlestick_ma_chart",
            "description": (
                "Generates a professional candlestick chart overlaid with 50-day and 200-day "
                "Moving Averages for a single stock. Call this when the user asks about trend "
                "direction, moving average crossovers (Golden Cross / Death Cross), or wants a "
                "technical chart that shows more than just price — e.g. 'show me TCS technically', "
                "'is HDFC above its 200-day MA', 'technical analysis of Reliance'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Stock ticker (e.g. 'TCS', 'RELIANCE', 'AAPL')."
                    },
                    "period": {
                        "type": "string",
                        "description": "Timeframe to display. Default '6mo'.",
                        "enum": ["1mo", "3mo", "6mo", "1y", "2y"]
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_rsi_chart",
            "description": (
                "Generates an RSI (Relative Strength Index) gauge chart with the price chart below it. "
                "Call this when the user asks about RSI, overbought/oversold conditions, or whether "
                "a stock is in a buying dip — e.g. 'is Nifty oversold?', 'show RSI for INFY', "
                "'what is the RSI of TCS right now'. Also call this automatically when an RSI alert "
                "triggers, to visually show the user why it fired."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Stock ticker (e.g. 'INFY', 'HDFCBANK')."
                    },
                    "period": {
                        "type": "string",
                        "description": "Lookback period. Default '3mo'.",
                        "enum": ["1mo", "3mo", "6mo", "1y"]
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_sector_heatmap",
            "description": (
                "Generates a color-coded sector heatmap showing today's percentage performance "
                "across key Indian sectors (IT, Banking, Auto, Pharma, FMCG, Energy, Metals, Infra) "
                "and major indices (Nifty50, BankNifty, S&P500, Nasdaq). Call this when the user asks "
                "for a market overview, 'what sectors are up today', 'market heatmap', or "
                "'how are different sectors doing'. No ticker needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_fundamental_radar",
            "description": (
                "Generates a radar (spider) chart comparing a stock's fundamentals — P/E, P/B, "
                "ROE, Profit Margin, Revenue Growth — against approximate market averages. "
                "Call this when the user asks for a fundamental comparison, valuation analysis, "
                "or whether a stock looks cheap/expensive — e.g. 'is TCS undervalued?', "
                "'show me fundamentals of HDFC', 'radar chart for Infosys'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Stock ticker (e.g. 'TCS', 'HDFCBANK', 'AAPL')."
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_support_resistance_chart",
            "description": (
                "Generates a price chart with automatically detected horizontal support and "
                "resistance levels drawn on it. Call this when the user asks where to buy, "
                "where the stock might bounce, key price levels, or entry/exit zones — "
                "e.g. 'where is Reliance support?', 'show support resistance for ICICI', "
                "'at what price should I buy TCS?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Stock ticker (e.g. 'RELIANCE', 'ICICIBANK')."
                    },
                    "period": {
                        "type": "string",
                        "description": "History to analyze. Default '6mo'.",
                        "enum": ["3mo", "6mo", "1y", "2y"]
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_market_alert",
            "description": (
                "Create a threshold alert that will proactively notify the user later when a "
                "condition is met - price crossing a level, a percentage move, or an RSI "
                "overbought/oversold signal. Works for individual stocks (e.g. RELIANCE, AAPL) "
                "and major indices (NIFTY50, BANKNIFTY, SENSEX, NASDAQ100). Call this whenever "
                "the user asks to be told/alerted/notified about a future price condition (e.g. "
                "'let me know when TCS hits 4000', 'alert me if Nifty drops 2%', 'tell me when "
                "Reliance is oversold'). For PERCENT_DROP/PERCENT_GAIN, the percentage is measured "
                "from the current price at the moment the alert is created - after calling this, "
                "always tell the user that exact baseline price so there's no ambiguity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker (e.g. 'RELIANCE', 'AAPL') or index name (NIFTY50, BANKNIFTY, SENSEX, NASDAQ100).",
                    },
                    "alert_type": {
                        "type": "string",
                        "description": "One of: PRICE_ABOVE, PRICE_BELOW, PERCENT_DROP, PERCENT_GAIN, RSI_OVERSOLD, RSI_OVERBOUGHT.",
                        "enum": ["PRICE_ABOVE", "PRICE_BELOW", "PERCENT_DROP", "PERCENT_GAIN", "RSI_OVERSOLD", "RSI_OVERBOUGHT"],
                    },
                    "target_value": {
                        "type": "string",
                        "description": (
                            "The numeric threshold, no units/symbols - e.g. '4000' for a price level, "
                            "'3' for a 3% move, '30' for RSI oversold (default 30 if user doesn't "
                            "specify), '70' for RSI overbought (default 70 if unspecified)."
                        ),
                    },
                    "permanent": {
                        "type": "boolean",
                        "description": (
                            "If true, the alert keeps watching even after it fires — "
                            "set this when the user asks for a 'permanent' or 'standing' "
                            "or 'recurring' watch. Default false (one-time)."
                        ),
                    },
                },
                "required": ["ticker", "alert_type", "target_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_market_alerts",
            "description": "List the user's currently active (not yet triggered) alerts. Call this when they ask what alerts they have set, or to check on something they set up earlier.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_market_alert",
            "description": "Cancel/delete one of the user's alerts by its ID. Call list_market_alerts first if you don't already know the ID from context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alert_id": {"type": "integer", "description": "The numeric ID of the alert to delete."}
                },
                "required": ["alert_id"],
            },
        },
    },
]


def execute_tool_call(db, telegram_id: str, tool_name: str, arguments: dict) -> str:
    """Executes a tool call and returns a short string result to feed back to the LLM."""
    from app.database.db import User, Message

    if tool_name == "save_user_profile":
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return "error: user not found"

        updated_fields = []
        if arguments.get("role"):
            user.role = arguments["role"]
            updated_fields.append("role")
        if arguments.get("sectors"):
            user.sectors = arguments["sectors"]
            updated_fields.append("sectors")
        if arguments.get("watchlist"):
            user.watchlist = arguments["watchlist"]
            updated_fields.append("watchlist")
        if arguments.get("briefing_time"):
            user.briefing_time = arguments["briefing_time"]
            updated_fields.append("briefing_time")

        # Mark onboarded once we have at least role + one other signal
        if user.role and (user.sectors or user.watchlist):
            user.onboarded = 1

        db.commit()
        return f"saved: {', '.join(updated_fields) if updated_fields else 'nothing new'}"

    if tool_name == "reset_conversation":
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            return "error: user not found"

        db.query(Message).filter(Message.telegram_id == telegram_id).delete()
        user.role = None
        user.sectors = None
        user.watchlist = None
        user.briefing_time = None
        user.onboarded = 0
        db.commit()
        return "reset: history and profile wiped"

    if tool_name == "get_stock_quote":
        from app.services.financial_data import get_stock_quote
        return json.dumps(get_stock_quote(arguments.get("query", "")))

    if tool_name == "generate_stock_chart":
        from app.services.financial_data import generate_stock_chart
        return json.dumps(generate_stock_chart(
            arguments.get("query", ""), 
            telegram_id,
            arguments.get("chart_type", "line"),
            arguments.get("period", "3mo")
        ))

    if tool_name == "generate_comparison_chart":
        from app.services.financial_data import generate_comparison_chart
        return json.dumps(generate_comparison_chart(
            arguments.get("queries", []), 
            telegram_id,
            arguments.get("period", "6mo")
        ))

    if tool_name == "generate_candlestick_ma_chart":
        from app.services.chart_engine import generate_candlestick_with_ma
        return json.dumps(generate_candlestick_with_ma(
            arguments.get("query", ""),
            telegram_id,
            arguments.get("period", "6mo")
        ))

    if tool_name == "generate_rsi_chart":
        from app.services.chart_engine import generate_rsi_gauge
        return json.dumps(generate_rsi_gauge(
            arguments.get("query", ""),
            telegram_id,
            arguments.get("period", "3mo")
        ))

    if tool_name == "generate_sector_heatmap":
        from app.services.chart_engine import generate_sector_heatmap
        return json.dumps(generate_sector_heatmap(telegram_id))

    if tool_name == "generate_fundamental_radar":
        from app.services.chart_engine import generate_fundamental_radar
        return json.dumps(generate_fundamental_radar(
            arguments.get("query", ""),
            telegram_id
        ))

    if tool_name == "generate_support_resistance_chart":
        from app.services.chart_engine import generate_support_resistance
        return json.dumps(generate_support_resistance(
            arguments.get("query", ""),
            telegram_id,
            arguments.get("period", "6mo")
        ))

    if tool_name == "get_company_news":
        from app.services.financial_data import get_company_news
        return json.dumps(get_company_news(arguments.get("query", "")))

    if tool_name == "get_sec_filings":
        from app.services.financial_data import get_sec_filings
        return json.dumps(get_sec_filings(arguments.get("query", "")))

    if tool_name == "get_company_fundamentals":
        from app.services.financial_data import get_company_fundamentals
        return json.dumps(get_company_fundamentals(arguments.get("query", "")))

    if tool_name == "calculate":
        from app.services.calculator import safe_calculate
        return json.dumps(safe_calculate(arguments.get("expression", "")))

    if tool_name == "connect_google_sheets":
        from app.integrations.google_oauth import build_auth_url
        link = build_auth_url(telegram_id)
        return json.dumps({"auth_link": link})

    if tool_name == "read_google_sheet":
        from app.integrations.google_sheets import get_sheet_data
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user or not user.google_refresh_token:
            return json.dumps({"error": "Not connected yet - the user needs to connect their Google account first."})
        return json.dumps(get_sheet_data(user.google_refresh_token, arguments.get("sheet_url", "")))

    if tool_name == "create_market_alert":
        from app.services.alert_engine import create_alert
        return json.dumps(create_alert(
            db, telegram_id,
            arguments.get("ticker", ""),
            arguments.get("alert_type", ""),
            arguments.get("target_value", ""),
            arguments.get("permanent", False),
        ))

    if tool_name == "list_market_alerts":
        from app.services.alert_engine import list_alerts
        alerts = list_alerts(db, telegram_id, active_only=True)
        return json.dumps({
            "alerts": [
                {
                    "id": a.id,
                    "ticker": a.ticker,
                    "alert_type": a.alert_type,
                    "target_value": a.target_value,
                    "baseline_price": a.baseline_price,
                }
                for a in alerts
            ]
        })

    if tool_name == "delete_market_alert":
        from app.services.alert_engine import delete_alert
        alert_id = arguments.get("alert_id")
        if alert_id is None:
            return json.dumps({"error": "alert_id is required"})
        return json.dumps(delete_alert(db, telegram_id, int(alert_id)))

    return f"error: unknown tool {tool_name}"