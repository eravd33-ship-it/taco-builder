"""Build a standalone Creator Desk app for the operating system you run this on.

    pip install pyinstaller
    python build_desktop.py

Result: dist/Creator Desk/  (Windows, Linux)  or  dist/Creator Desk.app  (macOS).
It cannot cross-compile: build on Windows to get a Windows app, on a Mac for a Mac app.
The app needs no Python on the target computer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEPARATOR = ";" if sys.platform == "win32" else ":"


def icon_arguments() -> list[str]:
    """The app icon: .ico for the Windows exe, .icns for the macOS app (Linux has no embedded icons)."""
    name = {"win32": "creator-desk.ico", "darwin": "creator-desk.icns"}.get(sys.platform)
    icon = ROOT / "packaging" / "icons" / name if name else None
    return ["--icon", str(icon)] if icon and icon.exists() else []


def main() -> int:
    ui = ROOT / "creator_bot" / "desktop" / "ui"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "Creator Desk",
        "--windowed",  # no console window on Windows / a proper .app on macOS
        "--paths", str(ROOT),
        "--add-data", f"{ui}{SEPARATOR}creator_bot/desktop/ui",
        "--collect-data", "tzdata",
        "--hidden-import", "webview",
        *icon_arguments(),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        str(ROOT / "packaging" / "creator_desk.py"),
    ]  # fmt: skip
    print("Running:", " ".join(command))
    code = subprocess.call(command, cwd=ROOT)
    if code == 0:
        print(f"\nDone. Your app is in: {ROOT / 'dist'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
