"""The desktop app's JSON API, called in-process (no HTTP, no window)."""

from datetime import timedelta

from .conftest import TODAY


def iso(days: int = 0) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


async def test_schedule_a_video_and_see_it_on_today(make_desk):
    desk = make_desk()
    status, video = await desk.call("POST", "/api/videos", {"title": "  ביקורת מצלמה ", "date": iso(7)})
    assert status == 201 and video["title"] == "ביקורת מצלמה"
    assert [s["state"] for s in video["steps"]] == ["current", "todo", "todo"]

    today = await desk.ok("GET", "/api/today")
    assert today["video_tasks"] == [] and today["open_count"] == 0
    assert [t["key"] for t in today["upcoming"]] == ["film"]  # filming is 3 days away

    await desk.ok("POST", "/api/videos", {"title": "ולוג", "date": iso(1)})  # film + edit clamp to today
    today = await desk.ok("GET", "/api/today")
    task = today["video_tasks"][0]
    assert (task["title"], task["verb"], task["key"]) == ("ולוג", "לצלם", "film")
    assert today["open_count"] == 1 and today["week"]["goal"] == 1


async def test_finishing_steps_celebrates_and_double_click_is_harmless(make_desk):
    desk = make_desk()
    video = (await desk.call("POST", "/api/videos", {"title": "סרטון", "date": iso(1)}))[1]

    first = await desk.ok("POST", f"/api/videos/{video['id']}/done", {"step": "film"})
    assert first["changed"] and not first["published"]
    again = await desk.ok("POST", f"/api/videos/{video['id']}/done", {"step": "film"})
    assert again == {"changed": False}
    await desk.settle()
    assert "הבא בתור" in desk.app.feed.to_json()["items"][0]["text"]  # motivation praised the step

    await desk.ok("POST", f"/api/videos/{video['id']}/done", {"step": "edit"})
    done = await desk.ok("POST", f"/api/videos/{video['id']}/done", {"step": "upload"})
    assert done["published"]
    await desk.settle()
    assert "באוויר" in desk.app.feed.to_json()["items"][0]["text"]

    videos = await desk.ok("GET", "/api/videos")
    assert videos["active"] == [] and videos["published"][0]["title"] == "סרטון"
    assert (await desk.ok("GET", "/api/today"))["week"]["done"] == 1


async def test_snooze_and_delete(make_desk):
    desk = make_desk()
    video = (await desk.call("POST", "/api/videos", {"title": "x", "date": iso(1)}))[1]
    assert (await desk.ok("POST", f"/api/videos/{video['id']}/snooze", {"step": "film"}))["changed"]
    assert (await desk.ok("GET", "/api/today"))["video_tasks"] == []  # film moved to tomorrow
    assert (await desk.call("POST", f"/api/videos/{video['id']}/snooze", {"step": "upload"}))[1] == {"changed": False}

    await desk.ok("DELETE", f"/api/videos/{video['id']}")
    assert (await desk.call("DELETE", f"/api/videos/{video['id']}"))[0] == 404


async def test_validation_and_routing_errors(make_desk):
    desk = make_desk()
    assert (await desk.call("POST", "/api/videos", {"title": "  ", "date": iso(3)}))[0] == 400
    assert (await desk.call("POST", "/api/videos", {"title": "x", "date": iso(-1)}))[1] == {"error": "התאריך כבר עבר"}
    assert (await desk.call("POST", "/api/videos", {"title": "x", "date": "מחר"}))[0] == 400
    assert (await desk.call("GET", "/api/nope"))[0] == 404
    assert (await desk.call("PATCH", "/api/videos"))[0] == 405
    assert (await desk.call("POST", "/api/videos/1/done", {"step": "film"}))[1] == {"changed": False}  # unknown video


async def test_routines(make_desk):
    desk = make_desk()
    assert (await desk.call("POST", "/api/routines", {"title": "x", "weekdays": []}))[0] == 400
    assert (await desk.call("POST", "/api/routines", {"title": "x", "weekdays": [9]}))[0] == 400
    assert (await desk.call("POST", "/api/routines", {"title": "", "weekdays": [1]}))[0] == 400

    status, routine = await desk.call("POST", "/api/routines", {"title": "💬 להגיב", "weekdays": [6, TODAY.weekday()]})
    assert status == 201 and routine["due_today"] and routine["days_label"].startswith("א׳")

    today = await desk.ok("GET", "/api/today")
    assert today["routines"] == [{"id": routine["id"], "title": "💬 להגיב", "done": False}] and today["open_count"] == 1

    assert (await desk.ok("POST", f"/api/routines/{routine['id']}/done"))["changed"]
    assert not (await desk.ok("POST", f"/api/routines/{routine['id']}/done"))["changed"]
    today = await desk.ok("GET", "/api/today")
    assert today["open_count"] == 0 and today["done_today"] == 1
    assert (await desk.ok("GET", "/api/routines"))["routines"][0]["done_today"]

    await desk.ok("DELETE", f"/api/routines/{routine['id']}")
    assert (await desk.call("POST", f"/api/routines/{routine['id']}/done"))[0] == 404


async def test_ideas_become_videos(make_desk):
    desk = make_desk()
    assert (await desk.call("POST", "/api/ideas", {"text": ""}))[0] == 400
    idea = (await desk.call("POST", "/api/ideas", {"text": "10 טיפים לעריכה"}))[1]
    assert (await desk.ok("GET", "/api/today"))["nudge_idea"]  # nothing in production -> suggest an idea

    assert (await desk.call("POST", f"/api/ideas/{idea['id']}/video", {"date": iso(-2)}))[0] == 400
    status, video = await desk.call("POST", f"/api/ideas/{idea['id']}/video", {"date": iso(5)})
    assert status == 201 and video["title"] == "10 טיפים לעריכה"
    assert (await desk.ok("GET", "/api/ideas"))["ideas"] == []  # used
    assert not (await desk.ok("GET", "/api/today"))["nudge_idea"]


async def test_goals(make_desk):
    desk = make_desk()
    assert (await desk.ok("GET", "/api/goals"))["uploads_per_week"] == 1
    goals = await desk.ok("PUT", "/api/goals", {"uploads_per_week": 3})
    assert goals["uploads_per_week"] == 3 and goals["week"]["goal"] == 3 and not goals["stats_enabled"]
    for bad in (0, 15, "3", True, 2.5):
        assert (await desk.call("PUT", "/api/goals", {"uploads_per_week": bad}))[0] == 400
    assert (await desk.ok("PUT", "/api/goals", {"views_per_month": 5000}))["views_per_month"] == 5000
    assert (await desk.ok("PUT", "/api/goals", {"views_per_month": 0}))["views_per_month"] is None


async def test_settings(make_desk):
    desk = make_desk()
    settings = await desk.ok("GET", "/api/settings")
    assert (settings["morning"], settings["evening"], settings["os_notifications"]) == ("09:00", "20:30", True)
    assert not settings["youtube"] and not settings["drive"]

    updated = await desk.ok("PUT", "/api/settings", {"morning": "07:45", "os_notifications": False, "autostart": True})
    assert updated["morning"] == "07:45" and not updated["os_notifications"] and updated["autostart"]
    assert not (await desk.ok("PUT", "/api/settings", {"autostart": False}))["autostart"]
    for bad in ("25:00", "9:00", "ab", 900):
        assert (await desk.call("PUT", "/api/settings", {"evening": bad}))[0] == 400


async def test_os_popups_only_when_the_window_is_not_in_front(make_desk):
    desk = make_desk()
    await desk.app.core.notify("📣 <b>שלום</b>\nגוף ההודעה")
    assert desk.shown == [("📣 שלום", "גוף ההודעה")]  # no window heartbeat yet -> pop-up

    await desk.ok("GET", "/api/feed", query={"focused": "1"})  # the window says "I am in front"
    await desk.app.core.notify("שנייה")
    assert len(desk.shown) == 1
    feed = await desk.ok("GET", "/api/feed")
    assert [item["text"] for item in feed["items"]] == ["שנייה", "📣 שלום\nגוף ההודעה"] and feed["unread"] == 2

    assert (await desk.ok("POST", "/api/feed/read"))["unread"] == 0

    await desk.ok("PUT", "/api/settings", {"os_notifications": False})
    desk.app._active_until = None
    await desk.app.core.notify("שקט")
    assert len(desk.shown) == 1  # pop-ups switched off; still in the inbox
    assert (await desk.ok("GET", "/api/feed"))["unread"] == 1


async def test_test_notification_reports_whether_the_os_showed_it(make_desk):
    desk = make_desk()
    assert await desk.ok("POST", "/api/test-notification") == {"shown": True}
    desk.app.show_os = lambda title, body: False
    assert await desk.ok("POST", "/api/test-notification") == {"shown": False}
    assert desk.app.feed.unread == 2


async def test_open_link_allows_only_known_https_hosts(make_desk):
    desk = make_desk()
    await desk.ok("POST", "/api/open", {"url": "https://drive.google.com/drive/folders/abc"})
    for bad in ("http://drive.google.com/x", "https://evil.example/x", "file:///etc/passwd", "javascript:alert(1)", ""):
        assert (await desk.call("POST", "/api/open", {"url": bad}))[0] == 400
    assert desk.opened == ["https://drive.google.com/drive/folders/abc"]


async def test_optional_integrations_report_not_configured(make_desk):
    desk = make_desk()
    assert (await desk.call("POST", "/api/stats/refresh"))[0] == 404
    assert (await desk.call("POST", "/api/drive/backup"))[0] == 404
    assert (await desk.ok("GET", "/api/today"))["channel"] is None


async def test_quit_calls_back(make_desk):
    desk = make_desk()
    called = []
    desk.app.quit_callback = lambda: called.append(True)
    await desk.ok("POST", "/api/quit")
    assert called == [True]


def test_build_settings_uses_the_data_folder_unless_db_path_is_set(tmp_path, monkeypatch):
    from creator_bot.config import Settings
    from creator_bot.desktop.app import build_settings

    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    settings = build_settings(Settings.from_env(require_token=False), tmp_path)
    assert settings.db_path == tmp_path / "creator_bot.db" and settings.owner_chat_id == 1

    monkeypatch.setenv("DB_PATH", str(tmp_path / "shared.db"))
    assert build_settings(Settings.from_env(require_token=False), tmp_path).db_path == tmp_path / "shared.db"
