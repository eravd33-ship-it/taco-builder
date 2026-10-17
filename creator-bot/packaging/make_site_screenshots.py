"""Regenerate the screenshots used by the website (assets/img/*.webp) from the real app.

    pip install playwright pillow && python -m playwright install chromium
    python packaging/make_site_screenshots.py            # screenshots + the social preview image
    python packaging/make_site_screenshots.py --og-only  # just the social preview image (assets/img/og.png)

It starts the app in memory (nothing on your computer is touched), fills it with clearly fake demo videos, routines
and ideas, and photographs each screen in a light and a dark theme. It never shows channel numbers or AI replies,
because there is nothing real to show for those.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import sys
import threading
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT.parent / "assets" / "img"
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from creator_bot.config import Settings  # noqa: E402
from creator_bot.desktop.app import DesktopApp  # noqa: E402
from creator_bot.desktop.runtime import Runtime  # noqa: E402
from creator_bot.desktop.server import DesktopServer  # noqa: E402
from creator_bot.storage import Database  # noqa: E402

SCREENS = {  # file name -> navigation button
    "today": "today",
    "videos": "videos",
    "routines": "routines",
    "ideas": "ideas",
    "goals": "goals",
}
VIEWPORT = {"width": 1100, "height": 700}
SCALE = 1.5


class NoAutostart:
    def is_enabled(self) -> bool:
        return False


def seed(api) -> None:
    today = date.today()
    iso = lambda days: (today + timedelta(days=days)).isoformat()  # noqa: E731
    published = api("POST", "/api/videos", {"title": "ולוג שבועי #12", "date": iso(0)})
    for step in ("film", "edit", "upload"):
        api("POST", f"/api/videos/{published['id']}/done", {"step": step})
    api("POST", "/api/videos", {"title": "5 טיפים לצילום בבית בלי ציוד יקר", "date": iso(1)})
    api("POST", "/api/videos", {"title": "ביקורת על מיקרופון חדש", "date": iso(6)})
    api("POST", "/api/videos", {"title": "איך אני עורך סרטון בשעה אחת", "date": iso(12)})
    every_day = list(range(7))
    comments = api("POST", "/api/routines", {"title": "💬 להגיב לתגובות", "weekdays": every_day})
    api("POST", "/api/routines", {"title": "📊 לבדוק אנליטיקס", "weekdays": every_day})
    api("POST", f"/api/routines/{comments['id']}/done")
    for idea in (
        "סרטון על טעויות נפוצות של יוצרים מתחילים",
        "מה יש בתיק הצילום שלי",
        "מענה לשאלות מהקהילה",
        "יום בחיים של יוצר תוכן",
    ):
        api("POST", "/api/ideas", {"text": idea})
    api("PUT", "/api/goals", {"uploads_per_week": 2})
    api("POST", "/api/feed/read")  # no unread badge in the pictures


def make_og(browser) -> None:
    """The 1200x630 picture shown when the site's link is shared."""
    icon = base64.b64encode((OUT / "icon.png").read_bytes()).decode()
    shot = base64.b64encode((OUT / "app-today-light.webp").read_bytes()).decode()
    html = f"""<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8"><style>
      * {{ box-sizing: border-box; margin: 0; }}
      body {{ width: 1200px; height: 630px; overflow: hidden; font-family: "Segoe UI", system-ui, Arial, sans-serif;
        background: linear-gradient(135deg, #d92d20, #ff7a45); color: #fff; display: flex; align-items: center; padding: 0 70px; gap: 40px; }}
      .text {{ flex: 1; }}
      .brand {{ display: flex; align-items: center; gap: 18px; font-size: 40px; font-weight: 800; margin-bottom: 28px; }}
      .brand img {{ width: 72px; height: 72px; }}
      h1 {{ font-size: 54px; line-height: 1.2; font-weight: 800; }}
      p {{ font-size: 28px; margin-top: 22px; opacity: .92; }}
      .win {{ width: 470px; height: 500px; border-radius: 22px 22px 0 0; background: #fff; overflow: hidden; align-self: flex-end;
        box-shadow: 0 20px 60px rgba(0,0,0,.35); }}
      .win img {{ width: 760px; display: block; margin-right: -40px; margin-top: 18px; }}
    </style><body>
      <div class="text"><div class="brand"><img src="data:image/png;base64,{icon}" alt="">Creator Desk</div>
        <h1>הערוץ שלך, מסודר.<br>סרטון אחרי סרטון.</h1><p>תזכורות, יעדים ורעיונות. אפליקציה למחשב.</p></div>
      <div class="win"><img src="data:image/webp;base64,{shot}" alt=""></div></body></html>"""
    page = browser.new_page(viewport={"width": 1200, "height": 630})
    page.set_content(html)
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "og.png"))
    page.close()
    print("wrote", OUT / "og.png")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    executable = os.environ.get("CHROMIUM") or None  # e.g. /opt/pw-browsers/chromium-1194/chrome-linux/chrome
    if "--og-only" in sys.argv:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=executable, args=["--no-sandbox"])
            make_og(browser)
            browser.close()
        return
    runtime = Runtime(
        lambda: DesktopApp(
            Settings(telegram_token="", owner_chat_id=1), Database(), show_os=lambda t, b: True, autostart=NoAutostart()
        ),
        tick_seconds=3600,
    )
    runtime.start()
    server = DesktopServer(("127.0.0.1", 0), runtime, secrets.token_urlsafe(16))
    runtime.app.port = server.port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token = re.search(r'name="token" content="([^"]+)"', urllib.request.urlopen(server.url).read().decode()).group(1)

    def api(method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            server.url.rstrip("/") + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    seed(api)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=executable, args=["--no-sandbox"])
        for theme in ("light", "dark"):
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE, color_scheme=theme, locale="he-IL")
            page.goto(server.url)
            page.wait_for_selector(".hero")
            for name, view in SCREENS.items():
                page.click(f'#nav button[data-view="{view}"]')
                page.wait_for_timeout(700)
                page.evaluate("() => document.querySelectorAll('#toasts .toast').forEach((t) => t.remove())")
                image = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
                image.save(OUT / f"app-{name}-{theme}.webp", quality=82, method=6)
                print("wrote", OUT / f"app-{name}-{theme}.webp", image.size)
            page.close()
        make_og(browser)
        browser.close()
    server.shutdown()
    runtime.stop()


if __name__ == "__main__":
    main()
