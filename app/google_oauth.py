import httpx
from urllib.parse import urlencode

from app.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def build_auth_url(telegram_id: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": str(telegram_id),
    }

    print("========== GOOGLE OAUTH DEBUG ==========")
    # print("PARAM KEYS:", [repr(k) for k in params.keys()])
    # print("PARAMS:", repr(params))

    url = f"{AUTH_ENDPOINT}?{urlencode(params)}"

    print("FINAL URL:", repr(url))
    # print("HAS BACKSLASH:", "\\" in url)
    # print("=========================================")

    return url


def exchange_code_for_tokens(code: str) -> dict:
    resp = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": GOOGLE_REDIRECT_URI,
        },
        timeout=10,
    )

    resp.raise_for_status()
    return resp.json()


def get_access_token(refresh_token: str) -> str:
    resp = httpx.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )

    resp.raise_for_status()
    return resp.json()["access_token"]
