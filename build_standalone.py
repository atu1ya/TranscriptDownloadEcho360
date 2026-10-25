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


def main() -> int:
    if shutil.which("pyinstaller") is None:
        print("PyInstaller is not installed. Run:  pip install pyinstaller")
        return 1

    write_entry_point()
    command = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",            # no console window behind the GUI
        "--name", APP_NAME,
        "--hidden-import", "tkinter",
        "--collect-submodules", "echo360dl",
        str(ENTRY),
    ]
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
        print(f'  "{APP_NAME}.exe"  -- copy it anywhere and double-click')
        print("  SmartScreen may warn on first run: More info -> Run anyway.")
    else:
        print(f'  "{APP_NAME}"  -- chmod +x and run')
    print("\nNote: browser login needs Playwright, which is NOT bundled.")
    print("Frozen builds use the 'Paste cookies' login instead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())