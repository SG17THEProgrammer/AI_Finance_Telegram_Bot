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
                        "description": "Preferred time for daily briefing, e.g. '08:00' or '8am IST'",
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
]


def execute_tool_call(db, telegram_id: str, tool_name: str, arguments: dict) -> str:
    """Executes a tool call and returns a short string result to feed back to the LLM."""
    from app.db import User, Message

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
        from app.financial_data import get_stock_quote
        return json.dumps(get_stock_quote(arguments.get("query", "")))

    if tool_name == "get_company_news":
        from app.financial_data import get_company_news
        return json.dumps(get_company_news(arguments.get("query", "")))

    if tool_name == "get_sec_filings":
        from app.financial_data import get_sec_filings
        return json.dumps(get_sec_filings(arguments.get("query", "")))

    if tool_name == "get_company_fundamentals":
        from app.financial_data import get_company_fundamentals
        return json.dumps(get_company_fundamentals(arguments.get("query", "")))

    if tool_name == "calculate":
        from app.calculator import safe_calculate
        return json.dumps(safe_calculate(arguments.get("expression", "")))

    return f"error: unknown tool {tool_name}"