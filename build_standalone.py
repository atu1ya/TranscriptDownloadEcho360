"""Build a standalone Echo360 Transcript Downloader for this computer's OS.

The result needs no Python on the machine that runs it: a single .exe on
Windows, an .app bundle on macOS, a single executable on Linux. Run it on
the OS you want to build for -- PyInstaller cannot cross-compile.

    pip install pyinstaller
    python build_standalone.py

Output lands in dist/.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Echo360 Transcript Downloader"
ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "_launch_gui.py"


def write_entry_point() -> None:
    """PyInstaller wants a plain script, not ``python -m``."""
    ENTRY.write_text(
        '"""Generated entry point for the frozen build."""\n'
        "from echo360dl.gui import main\n\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
        encoding="utf-8",
    )


def _playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return True


def main() -> int:
    if shutil.which("pyinstaller") is None:
        print("PyInstaller is not installed. Run:  pip install pyinstaller")
        return 1

    # A one-file build unpacks itself into %TEMP% at launch, which is also
    # what malware packers do: Defender flags those far more often than a
    # plain folder, so the folder is the default and one-file is opt-in.
    onefile = "--onefile" in sys.argv

    write_entry_point()
    command = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
        "--windowed",            # no console window behind the GUI
        "--name", APP_NAME,
        "--hidden-import", "tkinter",
        "--collect-submodules", "echo360dl",
        str(ENTRY),
    ]
    if _playwright_installed():
        # Bundling Playwright (package plus its node driver) is what lets the
        # frozen build log in through the browser at all: it cannot pip
        # install anything later. It drives whatever Chrome or Edge the
        # machine already has.
        command[-1:-1] = ["--collect-all", "playwright"]
    if sys.platform == "darwin":
        command += ["--osx-bundle-identifier", "au.edu.uwa.echo360dl"]

    print("Building:", " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    ENTRY.unlink(missing_ok=True)
    if result.returncode != 0:
        return result.returncode

    print("\nDone. Your build is in:", ROOT / "dist")
    if sys.platform == "darwin":
        print(f'  "{APP_NAME}.app"  -- drag it to Applications')
        print("  First launch: right-click -> Open (it is not code-signed).")
    elif sys.platform == "win32":
        if onefile:
            print(f'  "{APP_NAME}.exe"  -- copy it anywhere and double-click')
            print("  Defender flags one-file builds as a virus more often "
                  "than not; rebuild without --onefile if it does.")
        else:
            print(f'  "{APP_NAME}\\{APP_NAME}.exe"  -- keep the whole folder '
                  "together and double-click the .exe inside it")
        print("  SmartScreen may warn on first run: More info -> Run anyway.")
    else:
        print(f'  "{APP_NAME}"  -- chmod +x and run')
    if not _playwright_installed():
        print("\nNote: Playwright is not installed here, so it was not "
              "bundled and this build can only use 'Paste cookies'. "
              "Install playwright and rebuild for browser login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())