"""
Google OAuth2 for Sheets read access. Deliberately uses raw REST calls
(httpx) instead of google-auth-oauthlib/google-api-python-client - fewer
dependencies, faster to build under deadline, and this flow is simple enough
not to need the heavier SDKs.
"""

import httpx
from urllib.parse import urlencode

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def build_auth_url(telegram_id: str) -> str:
    """
    The link a user taps to connect their Google account. `state` carries the
    telegram_id through the flow so the callback knows which user to save the
    token against - Google echoes `state` back untouched.
    """
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # required to get a refresh_token back
        "prompt": "consent",        # forces refresh_token on repeat connects too
        "state": telegram_id,
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """Returns the token response dict (contains refresh_token, access_token, expires_in)."""
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
    """
    Mints a fresh short-lived access token from a stored refresh token. Called
    per-request rather than caching the access token ourselves - simpler,
    and access tokens are cheap to mint (no meaningful rate limit concern for
    our usage volume).
    """
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