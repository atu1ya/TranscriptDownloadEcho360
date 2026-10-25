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

NO_BROWSER_HINT = (
    "Browser login could not start a browser.\n\n"
    "Install Google Chrome or Microsoft Edge, which the app will use "
    "automatically, or use 'Paste cookies' instead."
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
        def start(channel: str | None):
            """Open a window, either with Playwright's Chromium or ``channel``."""
            options: dict[str, object] = {"headless": False}
            if channel:
                options["channel"] = channel
            if persist:
                context = playwright.chromium.launch_persistent_context(
                    session_store.BROWSER_PROFILE, args=["--no-first-run"],
                    **options,
                )
                return context, (context.pages[0] if context.pages
                                 else context.new_page())
            browser = playwright.chromium.launch(**options)
            context = browser.new_context()
            return context, context.new_page()

        def open_browser():
            # Prefer a browser that is already here, so most people never
            # wait on a download.
            attempts: list[str | None] = []
            if bootstrap.browser_installed():
                attempts.append(None)
            channel = bootstrap.system_browser()
            if channel:
                attempts.append(channel)

            last: PlaywrightError | None = None
            for candidate in attempts:
                try:
                    return start(candidate)
                except PlaywrightError as exc:
                    last = exc
                    say(f"{candidate or 'Chromium'} would not start -- "
                        "trying another browser.")

            # Nothing usable on this computer: fetch our own Chromium, which
            # a frozen build cannot do.
            if bootstrap.is_frozen():
                raise Echo360Error(bootstrap.frozen_text())
            say("No usable browser found -- downloading Chromium "
                "(one time only).")
            bootstrap.install_browser(say)
            try:
                return start(None)
            except PlaywrightError as exc:
                raise Echo360Error(
                    f"{NO_BROWSER_HINT}\n\nDetails: {last or exc}"
                ) from exc

        context, page = open_browser()

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