"""
Reads data from a Google Sheet the user has shared/connected access to.
"""

import re
import httpx

from app.google_oauth import get_access_token

_SHEET_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def extract_sheet_id(url_or_id: str) -> str:
    """Accepts a full Google Sheets URL or a bare sheet ID, returns the ID."""
    match = _SHEET_ID_PATTERN.search(url_or_id)
    if match:
        return match.group(1)
    # Assume they already passed a bare ID if no URL pattern matched
    return url_or_id.strip()


def get_sheet_data(refresh_token: str, sheet_url_or_id: str, max_rows: int = 300) -> dict:
    """
    Returns {"values": [[...], ...], "range": ...} on success, or {"error": ...}
    on failure (not found, no access, bad token, etc.) - never fabricates data
    if the fetch fails.
    """
    sheet_id = extract_sheet_id(sheet_url_or_id)

    try:
        access_token = get_access_token(refresh_token)
    except Exception as exc:
        return {"error": f"Could not authenticate with Google - the connection may have expired: {exc}"}

    try:
        resp = httpx.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/A1:Z{max_rows}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if resp.status_code == 404:
            return {"error": "Sheet not found - check the link is correct."}
        if resp.status_code == 403:
            return {"error": "No access to this sheet - make sure it's shared with the connected Google account."}
        resp.raise_for_status()
        data = resp.json()
        values = data.get("values", [])
        if not values:
            return {"error": "This sheet appears to be empty."}
        return {"values": values, "row_count": len(values)}
    except httpx.HTTPStatusError as exc:
        return {"error": f"Could not read the sheet: {exc}"}
    except Exception as exc:
        return {"error": f"Unexpected error reading the sheet: {exc}"}