"""One-time, self-service setup of the browser-login dependency.

Browser login needs two things: the ``playwright`` Python package and a
Chromium-family browser for it to drive. Rather than asking the user to
open a terminal, the app sorts both out itself:

* the package is pip-installed into the app's own interpreter on demand;
* the browser is usually one the computer already has -- Playwright can
  drive an installed Chrome or Edge, so most people download nothing at
  all. Only when neither is present does it fetch its own Chromium.

The package install works when the app runs from source (including the
virtualenv the launchers create). It cannot work inside a PyInstaller
build, which has no pip and no writable site-packages -- see ``describe()``.
"""

from __future__ import annotations

import os
import shutil
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


# Chrome and Edge install in predictable places, and Playwright can drive
# either as a "channel". Checking the disk beats launching one to find out.
_WINDOWS_BROWSERS = (
    ("chrome", r"Google\Chrome\Application\chrome.exe"),
    ("msedge", r"Microsoft\Edge\Application\msedge.exe"),
)
_MACOS_BROWSERS = (
    ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
)
_LINUX_BROWSERS = (
    ("chrome", ("google-chrome", "google-chrome-stable")),
    ("msedge", ("microsoft-edge", "microsoft-edge-stable")),
)


def system_browser() -> str | None:
    """The Playwright channel for a Chrome/Edge already installed here.

    ``None`` when the computer has neither, in which case browser login
    falls back to Playwright's own Chromium download.
    """
    if sys.platform == "win32":
        roots = [os.environ.get(name) for name in
                 ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)",
                  "LOCALAPPDATA")]
        for channel, relative in _WINDOWS_BROWSERS:
            for root in roots:
                if root and os.path.exists(os.path.join(root, relative)):
                    return channel
        return None
    if sys.platform == "darwin":
        for channel, path in _MACOS_BROWSERS:
            if os.path.exists(path):
                return channel
        return None
    for channel, commands in _LINUX_BROWSERS:
        if any(shutil.which(command) for command in commands):
            return channel
    return None


def describe() -> str:
    """What, if anything, still needs installing."""
    if package_installed():
        if browser_installed() or system_browser():
            return READY
        return FROZEN if is_frozen() else MISSING_BROWSER
    return FROZEN if is_frozen() else MISSING_PACKAGE


def frozen_text() -> str:
    """Why browser login is unavailable in a standalone build, and the way out."""
    if package_installed():
        # Playwright is bundled; it just has no browser to drive.
        return (
            "Browser login needs Google Chrome or Microsoft Edge, and this "
            "computer has neither.\n\n"
            "Install either one and try again, or use 'Paste cookies', "
            "which needs nothing extra."
        )
    return (
        "This standalone build cannot set up browser login.\n\n"
        "Use 'Paste cookies' instead -- the dialog walks you through it -- "
        "or run the app from source for the one-click login."
    )


def prompt_text(state: str) -> str:
    """The question to put to the user before spending their bandwidth."""
    if state == MISSING_BROWSER:
        return (
            "Browser login needs a private copy of Chromium, which has not "
            f"been downloaded yet ({DOWNLOAD_SIZE_HINT}).\n\n"
            "Download it now? It is a one-time setup -- after this, logging "
            "in is a single click.\n\n"
            "Alternatives: install Google Chrome or Microsoft Edge, which "
            "the app will drive instead with no download at all, or use "
            "'Paste cookies'."
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
        raise Echo360Error(frozen_text())
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