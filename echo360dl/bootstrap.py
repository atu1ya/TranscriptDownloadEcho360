"""One-time, self-service installation of the browser-login dependency.

Browser login needs two things: the ``playwright`` Python package and the
Chromium build it drives. Rather than asking the user to open a terminal,
the app installs both into its own interpreter on demand.

This works when the app runs from source (including the virtualenv the
launchers create). It cannot work inside a PyInstaller build, which has no
pip and no writable site-packages -- see ``describe()``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable

from .core import Echo360Error

StatusFn = Callable[[str], None]

READY = "ready"
MISSING_PACKAGE = "missing_package"
MISSING_BROWSER = "missing_browser"
FROZEN = "frozen"

# Roughly what the user is committing to, so the prompt can be honest.
DOWNLOAD_SIZE_HINT = "about 150 MB"


def is_frozen() -> bool:
    """True inside a PyInstaller build, where pip is not available."""
    return bool(getattr(sys, "frozen", False))


def package_installed() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def browser_installed() -> bool:
    """True when Playwright's Chromium has actually been downloaded."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            return os.path.exists(playwright.chromium.executable_path)
    except Exception:
        return False


def describe() -> str:
    """What, if anything, still needs installing."""
    if package_installed():
        return READY if browser_installed() else MISSING_BROWSER
    return FROZEN if is_frozen() else MISSING_PACKAGE


def prompt_text(state: str) -> str:
    """The question to put to the user before spending their bandwidth."""
    if state == MISSING_BROWSER:
        return (
            "Browser login needs a private copy of Chromium, which has not "
            f"been downloaded yet ({DOWNLOAD_SIZE_HINT}).\n\n"
            "Download it now? It is a one-time setup -- after this, logging "
            "in is a single click.\n\n"
            "You can skip this and use 'Paste cookies' instead."
        )
    return (
        "Browser login is the easy way to sign in: a browser window opens, "
        "you log in to Echo360 as usual, and the app takes it from there -- "
        "no DevTools, no copying cookies.\n\n"
        f"Setting it up downloads {DOWNLOAD_SIZE_HINT} and takes a few "
        "minutes. It happens once.\n\n"
        "Install it now?"
    )


def _run(command: list[str], on_status: StatusFn | None, what: str) -> None:
    """Run a setup command, relaying its output line by line."""

    def say(message: str) -> None:
        if on_status:
            on_status(message)

    say(f"{what}...")
    creation_flags = 0
    if sys.platform == "win32":
        # Stops a console window flashing up when launched via pythonw.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise Echo360Error(f"could not start the installer ({exc})") from exc

    assert process.stdout is not None
    tail: list[str] = []
    for line in process.stdout:
        line = line.rstrip()
        if not line:
            continue
        tail.append(line)
        del tail[:-12]
        if any(marker in line.lower() for marker in
               ("downloading", "installing", "collecting", "successfully", "error")):
            say(f"  {line}")

    if process.wait() != 0:
        detail = "\n".join(tail[-6:])
        raise Echo360Error(f"{what} failed.\n\n{detail}")
    say(f"{what}: done.")


def install_package(on_status: StatusFn | None = None) -> None:
    if is_frozen():
        raise Echo360Error(
            "this standalone build cannot install extra packages. Use "
            "'Paste cookies' to log in, or run the app from source."
        )
    try:
        _run([sys.executable, "-m", "pip", "install", "--upgrade", "playwright"],
             on_status, "Installing the playwright package")
    except Echo360Error as exc:
        raise Echo360Error(_explain(str(exc))) from exc


def _explain(message: str) -> str:
    """Turn a raw installer failure into something a non-expert can act on."""
    lowered = message.lower()
    if "externally-managed-environment" in lowered:
        return (
            message + "\n\nThis Python does not allow installing packages "
            "system-wide. Start the app with run.sh (or run.bat), which "
            "creates its own environment, and try again."
        )
    if "no module named pip" in lowered:
        return (
            message + "\n\nThis Python has no pip. Start the app with "
            "run.sh (or run.bat), which builds a complete environment."
        )
    if any(marker in lowered for marker in
           ("download failure", "timed out", "connection", "proxy", "certificate",
            "getaddrinfo", "econnrefused")):
        return (
            message + "\n\nThat looks like a network problem -- a firewall, "
            "proxy or filtered connection blocking the download. On a "
            "university or workplace network, try again from home, or use "
            "'Paste cookies' instead, which needs no download."
        )
    return message


def install_browser(on_status: StatusFn | None = None) -> None:
    try:
        _run([sys.executable, "-m", "playwright", "install", "chromium"],
             on_status, "Downloading Chromium")
    except Echo360Error as exc:
        raise Echo360Error(_explain(str(exc))) from exc


def ensure(on_status: StatusFn | None = None) -> None:
    """Install whatever browser login is still missing. Safe to re-run."""
    state = describe()
    if state == READY:
        return
    if state == FROZEN:
        raise Echo360Error(
            "this standalone build cannot set up browser login. Use 'Paste "
            "cookies' instead, or run the app from source to get the "
            "one-click login."
        )
    if state == MISSING_PACKAGE:
        install_package(on_status)
        if not package_installed():
            raise Echo360Error(
                "playwright was installed but still cannot be imported. "
                "Restart the app and try again."
            )
    if not browser_installed():
        install_browser(on_status)
    if describe() != READY:
        raise Echo360Error(
            "setup finished but Chromium is still missing. Use 'Paste "
            "cookies' to log in for now."
        )