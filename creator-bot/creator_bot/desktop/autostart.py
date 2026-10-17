"""Start Creator Desk (minimized) when the user logs in, so reminders always arrive.

Only plain per-user files are written (no admin rights, no registry):
  Windows – a .cmd in the Startup folder     macOS – a LaunchAgent plist
  Linux   – an XDG autostart .desktop file
"""

from __future__ import annotations

import os
import plistlib
import shlex
import sys
from pathlib import Path

NAME = "Creator Desk"
PROJECT_DIR = Path(__file__).resolve().parents[2]  # the folder that contains the creator_bot package


def working_dir(frozen: bool | None = None) -> Path:
    """Source: the folder holding the creator_bot package. Packaged app: the folder of the executable."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    return Path(sys.executable).resolve().parent if frozen else PROJECT_DIR


def launch_command(frozen: bool | None = None, python: str | None = None) -> list[str]:
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    python = python or sys.executable
    return [python, "--minimized"] if frozen else [python, "-m", "creator_bot.desktop", "--minimized"]


def _windowless(python: str) -> str:
    """On Windows prefer pythonw.exe, so no console window opens."""
    path = Path(python)
    candidate = path.with_name("pythonw.exe")
    return str(candidate) if path.name.lower() == "python.exe" and candidate.exists() else python


def _exec_field(command: list[str], directory: Path) -> str:
    """A `.desktop` Exec= line: run through sh so we can cd first. Escapes per the Desktop Entry spec."""
    inner = f"cd {shlex.quote(str(directory))} && exec {shlex.join(command)}"
    escaped = inner.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    return f'sh -c "{escaped}"'


class Autostart:
    def __init__(self, platform: str | None = None, home: Path | None = None, command: list[str] | None = None) -> None:
        self.platform = platform or sys.platform
        self.home = home or Path.home()
        self._command = command

    @property
    def command(self) -> list[str]:
        return self._command or launch_command()

    @property
    def path(self) -> Path:
        if self.platform == "win32":
            appdata = Path(os.environ.get("APPDATA") or self.home / "AppData" / "Roaming")
            return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Creator Desk.cmd"
        if self.platform == "darwin":
            return self.home / "Library" / "LaunchAgents" / "com.creatordesk.app.plist"
        config = Path(os.environ.get("XDG_CONFIG_HOME") or self.home / ".config")
        return config / "autostart" / "creator-desk.desktop"

    def is_enabled(self) -> bool:
        return self.path.exists()

    def enable(self) -> Path:
        path, command, directory = self.path, self.command, working_dir()
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.platform == "win32":
            command = [_windowless(command[0]), *command[1:]]
            quoted = " ".join(f'"{part}"' for part in command)
            path.write_text(f'@echo off\r\ncd /d "{directory}"\r\nstart "" /min {quoted}\r\n', encoding="utf-8")
        elif self.platform == "darwin":
            plist = {
                "Label": "com.creatordesk.app",
                "ProgramArguments": command,
                "WorkingDirectory": str(directory),
                "RunAtLoad": True,
            }
            path.write_bytes(plistlib.dumps(plist))
        else:
            path.write_text(
                "[Desktop Entry]\nType=Application\n"
                f"Name={NAME}\nComment=Reminders for your YouTube channel\n"
                f"Exec={_exec_field(command, directory)}\nTerminal=false\n"
                "X-GNOME-Autostart-enabled=true\n",
                encoding="utf-8",
            )
        return path

    def disable(self) -> None:
        self.path.unlink(missing_ok=True)
