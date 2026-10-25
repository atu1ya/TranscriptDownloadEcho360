#!/bin/bash
# ===================================================================
#  Echo360 Transcript Downloader -- macOS launcher
#  Double-click this file in Finder. It sets everything up the
#  first time. (If macOS refuses: right-click -> Open -> Open.)
# ===================================================================
cd "$(dirname "$0")" || exit 1

say_gui() {   # show a dialog, because a double-clicked script has no terminal to read
  osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"Echo360 Transcript Downloader\"" >/dev/null 2>&1
}

find_python() {
  for candidate in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 \
                   /Library/Frameworks/Python.framework/Versions/Current/bin/python3; do
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

PY="$(find_python)" || {
  say_gui "Python 3.10 or newer was not found.

This app needs it. The download page will now open: install Python from python.org (the normal installer, no options to change), then double-click this file again."
  open "https://www.python.org/downloads/macos/"
  exit 1
}

if ! "$PY" -c 'import tkinter' >/dev/null 2>&1; then
  say_gui "Your Python is missing Tkinter, the part that draws windows.

Easiest fix: install Python from python.org (it includes Tkinter) and double-click this file again.

Using Homebrew instead? Run:  brew install python-tk"
  open "https://www.python.org/downloads/macos/"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Setting up for first use. This takes a minute, one time only..."
  if ! "$PY" -m venv .venv; then
    say_gui "Could not create the virtual environment. Try reinstalling Python from python.org."
    exit 1
  fi
  ./.venv/bin/python -m pip install --quiet --upgrade pip
  if ! ./.venv/bin/python -m pip install --quiet requests; then
    say_gui "Could not download the 'requests' package. Check your internet connection and try again."
    exit 1
  fi
fi

exec ./.venv/bin/python -m echo360dl "$@"