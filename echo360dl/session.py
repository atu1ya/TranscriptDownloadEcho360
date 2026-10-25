"""Where the Echo360 session cookies live between runs."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone

from .core import AUTH_COOKIE

APP_DIR = os.path.join(os.path.expanduser("~"), ".echo360dl")
SESSION_FILE = os.path.join(APP_DIR, "session.json")
BROWSER_PROFILE = os.path.join(APP_DIR, "browser-profile")


def save(cookies: dict[str, str], host: str) -> str:
    """Store cookies for reuse. Owner-readable only -- this is a live login."""
    os.makedirs(APP_DIR, exist_ok=True)
    payload = {
        "host": host,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cookies": cookies,
    }
    with open(SESSION_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    try:
        os.chmod(SESSION_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return SESSION_FILE


def load() -> tuple[dict[str, str], str, str]:
    """Return ``(cookies, host, saved_at)``; empties if nothing is stored."""
    try:
        with open(SESSION_FILE, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}, "", ""
    cookies = payload.get("cookies") or {}
    if not isinstance(cookies, dict) or AUTH_COOKIE not in cookies:
        return {}, payload.get("host", ""), payload.get("saved_at", "")
    return cookies, payload.get("host", ""), payload.get("saved_at", "")


def clear() -> None:
    for path in (SESSION_FILE,):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass