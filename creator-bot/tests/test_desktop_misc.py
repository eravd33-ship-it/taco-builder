"""Notifications, autostart files and data folders (the OS-specific parts)."""

import plistlib
from pathlib import Path

import pytest

from creator_bot.desktop.autostart import Autostart, launch_command
from creator_bot.desktop.notifier import notification_command, split_title, to_plain
from creator_bot.desktop.paths import data_dir


def test_module_texts_become_plain_text_with_a_link():
    plain, link = to_plain(
        '📁 פתחתי תיקייה ל<b>טיול &amp; ים</b>:\n<a href="https://drive.google.com/x?a=1&amp;b=2">פתיחה</a>'
    )
    assert plain == "📁 פתחתי תיקייה לטיול & ים:\nפתיחה"
    assert link == "https://drive.google.com/x?a=1&b=2"
    assert to_plain("<i>hi</i>") == ("hi", None)
    assert to_plain("🚀 השבוע: 1/2  ▓▓▓▓▓░░░░░ 50%\nעוד סרטון") == ("🚀 השבוע: 1/2\nעוד סרטון", None)


def test_title_and_body_split_for_popups():
    assert split_title("כותרת\nשורה אחת\n\nשורה שתיים") == ("כותרת", "שורה אחת שורה שתיים")
    assert split_title("")[0] == "Creator Desk"
    title, body = split_title("א" * 200 + "\n" + "ב" * 500)
    assert len(title) == 70 and len(body) == 240


def test_notification_commands_per_platform():
    command, env = notification_command('say "hi"', 'a\\b "c"', "darwin")
    assert command[:2] == ["osascript", "-e"]
    assert command[2] == 'display notification "a\\\\b \\"c\\"" with title "say \\"hi\\""'

    command, env = notification_command("כותרת", "גוף", "win32")
    assert command[0] == "powershell" and env["CD_TITLE"] == "כותרת" and env["CD_BODY"] == "גוף"
    assert "כותרת" not in " ".join(command)  # user text travels in the environment, never in the script

    assert notification_command("t", "b", "linux", which=lambda name: "/usr/bin/notify-send")[0][0] == "notify-send"
    assert notification_command("t", "b", "linux", which=lambda name: None) is None


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("linux", ".local/share/CreatorDesk"),
        ("darwin", "Library/Application Support/CreatorDesk"),
        ("win32", "AppData/Roaming/CreatorDesk"),
    ],
)
def test_data_folders(platform, expected, tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    assert data_dir(platform, tmp_path) == tmp_path / expected


def test_launch_command_from_source_and_when_packaged():
    assert launch_command(False, "/venv/bin/python") == ["/venv/bin/python", "-m", "creator_bot.desktop", "--minimized"]
    assert launch_command(True, "/apps/CreatorDesk") == ["/apps/CreatorDesk", "--minimized"]


def test_autostart_on_linux(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    autostart = Autostart("linux", tmp_path, ["/my env/bin/python", "-m", "creator_bot.desktop", "--minimized"])
    assert not autostart.is_enabled()
    path = autostart.enable()
    assert path == tmp_path / ".config/autostart/creator-desk.desktop" and autostart.is_enabled()
    text = path.read_text()
    assert text.startswith("[Desktop Entry]") and "Terminal=false" in text
    assert "'/my env/bin/python' -m creator_bot.desktop --minimized" in text  # spaces are quoted
    autostart.disable()
    autostart.disable()  # twice is fine
    assert not autostart.is_enabled()


def test_autostart_on_macos(tmp_path):
    autostart = Autostart("darwin", tmp_path, ["/usr/bin/python3", "-m", "creator_bot.desktop", "--minimized"])
    plist = plistlib.loads(autostart.enable().read_bytes())
    assert plist["RunAtLoad"] is True and plist["ProgramArguments"][-1] == "--minimized"
    assert Path(plist["WorkingDirectory"]).name == "creator-bot"


def test_autostart_on_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    autostart = Autostart(
        "win32", tmp_path, [r"C:\Users\me\app\.venv\Scripts\python.exe", "-m", "creator_bot.desktop", "--minimized"]
    )
    path = autostart.enable()
    assert path.name == "Creator Desk.cmd" and "Startup" in path.parts
    text = path.read_text()
    assert "cd /d " in text and 'start "" /min "' in text and "--minimized" in text
