"""Start the desktop app:  python -m creator_bot.desktop

Opens a native window (pywebview) when available, otherwise your browser. If
Creator Desk is already running, it just opens a window onto that instance.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import secrets
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from ..config import Settings, load_dotenv
from ..storage import Database
from . import APP_TITLE, __version__
from .app import DesktopApp, build_settings
from .autostart import Autostart
from .paths import data_dir
from .runtime import Runtime
from .server import DesktopServer

DEFAULT_PORT = 8765
log = logging.getLogger("creator_bot.desktop")


def setup_logging(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            folder / "creator-desk.log", maxBytes=500_000, backupCount=2, encoding="utf-8"
        )
    ]
    if sys.stderr is not None:  # a windowed (no console) build has no stderr
        handlers.append(logging.StreamHandler())
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO, handlers=handlers)


def running_instance(port: int) -> bool:
    """True if a Creator Desk is already serving on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as response:
            return json.load(response).get("app") == "creator-desk"
    except (OSError, ValueError):
        return False


def open_window(url: str, minimized: bool, quit_event: threading.Event | None = None) -> bool:
    """Show the UI in a native window and block until it is closed. False if no native window is available."""
    try:
        import webview

        webview.create_window(APP_TITLE, url, width=1120, height=780, min_size=(420, 560), minimized=minimized)
        if quit_event is not None:  # the in-app "quit" button closes the window

            def close_on_quit() -> None:
                quit_event.wait()
                for window in webview.windows:
                    window.destroy()

            threading.Thread(target=close_on_quit, daemon=True).start()
        webview.start()
        return True
    except Exception as error:  # missing GUI libraries etc. – the browser fallback still works
        log.info("No native window available (%s) – using the browser", type(error).__name__)
        log.debug("Details", exc_info=True)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="creator_bot.desktop", description=f"{APP_TITLE} {__version__}")
    parser.add_argument("--minimized", action="store_true", help="start minimized (used by autostart)")
    parser.add_argument("--no-window", action="store_true", help="run the reminders and server only")
    parser.add_argument("--browser", action="store_true", help="use the browser instead of a native window")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--enable-autostart", action="store_true", help="start with the computer, then exit")
    parser.add_argument("--disable-autostart", action="store_true")
    args = parser.parse_args(argv)

    folder = data_dir()
    setup_logging(folder)
    if args.enable_autostart or args.disable_autostart:
        autostart = Autostart()
        if args.enable_autostart:
            print(f"OK – {autostart.enable()}")
        else:
            autostart.disable()
            print("OK – autostart removed")
        return 0

    if running_instance(args.port):
        log.info("Creator Desk is already running – opening it")
        if not (args.no_window or args.minimized):
            url = f"http://127.0.0.1:{args.port}/"
            if args.browser or not open_window(url, False):
                webbrowser.open(url)
        return 0

    load_dotenv(Path.cwd() / ".env")
    load_dotenv(folder / ".env")  # lets a packaged app (no useful working directory) find its settings
    settings = build_settings(Settings.from_env(require_token=False), folder)

    def create_app() -> DesktopApp:
        return DesktopApp(settings, Database(settings.db_path))

    runtime = Runtime(create_app)
    runtime.start()
    try:
        server = DesktopServer(("127.0.0.1", args.port), runtime, secrets.token_urlsafe(32))
    except OSError:  # the port belongs to something else: take any free one
        server = DesktopServer(("127.0.0.1", 0), runtime, secrets.token_urlsafe(32))
    threading.Thread(target=server.serve_forever, name="creator-desk-http", daemon=True).start()
    log.info("%s %s is running at %s (data: %s)", APP_TITLE, __version__, server.url, settings.db_path.parent)

    try:
        if args.no_window or (args.minimized and args.browser):
            print(f"{APP_TITLE} is running – {server.url}  (Ctrl+C to quit)")
            wait_for_quit(runtime)
        elif args.browser or not open_window(server.url, args.minimized, runtime.quit_event):
            if not args.minimized:
                webbrowser.open(server.url)
            print(f"{APP_TITLE} is running – {server.url}  (Ctrl+C or the quit button to stop)")
            wait_for_quit(runtime)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        runtime.stop()
    return 0


def wait_for_quit(runtime: Runtime) -> None:
    while not runtime.quit_event.wait(0.5):  # short waits, so Ctrl+C works on Windows too
        pass


if __name__ == "__main__":
    sys.exit(main())
