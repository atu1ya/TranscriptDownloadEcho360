"""Browser-assisted login.

Echo360 at most universities sits behind institutional SSO with MFA, so this
never touches credentials: it opens a real browser window at the Echo360
page, waits for *you* to finish logging in, and then copies the resulting
session cookies out of the browser.
"""

from __future__ import annotations

import time
from typing import Callable

from . import bootstrap
from .core import AUTH_COOKIE, Echo360Error

PLAYWRIGHT_HINT = (
    "Browser login needs Playwright, which the app can install for you.\n\n"
    "Click 'Log in with browser' and say yes when it offers to set it up.\n\n"
    "In a standalone build that is not possible -- use 'Paste cookies' "
    "instead, or run the app from source."
)


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def login(
    start_url: str,
    *,
    timeout: float = 300.0,
    persist: bool = True,
    on_status: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Open a browser at ``start_url`` and return cookies once logged in.

    Blocks until the Echo360 auth cookie appears, the window is closed, or
    ``timeout`` seconds pass.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - depends on local install
        raise Echo360Error(PLAYWRIGHT_HINT) from exc

    from . import session as session_store

    def say(message: str) -> None:
        if on_status:
            on_status(message)

    say("Opening browser -- log in to Echo360 in the window that appears.")
    deadline = time.monotonic() + timeout

    with sync_playwright() as playwright:
        def start():
            if persist:
                context = playwright.chromium.launch_persistent_context(
                    session_store.BROWSER_PROFILE, headless=False,
                    args=["--no-first-run"],
                )
                return context, (context.pages[0] if context.pages
                                 else context.new_page())
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context()
            return context, context.new_page()

        try:
            context, page = start()
        except PlaywrightError as exc:
            # The usual cause is a playwright package whose Chromium was
            # never downloaded; fetch it once and try again.
            if "executable doesn" not in str(exc).lower():
                raise Echo360Error(f"could not start Chromium ({exc})") from exc
            say("Chromium is missing -- downloading it now (one time only).")
            bootstrap.install_browser(say)
            try:
                context, page = start()
            except PlaywrightError as retry_exc:
                raise Echo360Error(
                    f"could not start Chromium ({retry_exc}).\n\n{PLAYWRIGHT_HINT}"
                ) from retry_exc

        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightError:
            say("Could not load the page automatically -- navigate there manually.")

        cookies: dict[str, str] = {}
        while time.monotonic() < deadline:
            try:
                raw = context.cookies()
            except PlaywrightError:
                break  # window closed
            jar = {entry["name"]: entry["value"] for entry in raw}
            if jar.get(AUTH_COOKIE):
                cookies = jar
                break
            say("Waiting for login to finish...")
            time.sleep(1.5)

        try:
            context.close()
        except PlaywrightError:
            pass

    if not cookies:
        raise Echo360Error(
            "login did not complete -- no Echo360 session cookie was set."
        )
    say("Logged in.")
    return cookies