from datetime import timedelta

from creator_bot.modules.ideas import IdeasModule
from creator_bot.modules.routines import RoutinesModule
from creator_bot.modules.videos import VideosModule

from .conftest import TODAY


def test_routines_due_by_weekday_and_done_once(make_core):
    service = make_core([RoutinesModule]).get("routines").service
    daily = service.add("להגיב לתגובות", set(range(7)), TODAY)
    tomorrow_only = service.add("פוסט בקהילה", {(TODAY + timedelta(days=1)).weekday()}, TODAY)

    assert [r.id for r in service.open_on(TODAY)] == [daily.id]
    assert service.mark_done(daily.id, TODAY) is True
    assert service.mark_done(daily.id, TODAY) is False  # double tap
    assert service.open_on(TODAY) == []
    assert {r.id for r in service.open_on(TODAY + timedelta(days=1))} == {daily.id, tomorrow_only.id}


def test_days_are_shown_in_week_order(make_core):
    routines = make_core([RoutinesModule]).get("routines")
    assert routines.fmt_days({6, 1}) == "א׳, ג׳"  # Sunday first
    assert routines.fmt_days(set(range(7))) == "כל יום"


async def test_idea_marked_used_when_turned_into_video(make_core):
    core = make_core([VideosModule, IdeasModule])
    ideas = core.get("ideas").service
    idea = ideas.add("10 טיפים לעריכה", TODAY)

    section = await core.get("ideas").morning_section(TODAY)
    assert "רעיון אחד" in section.text  # nothing in production -> suggest an idea

    video = core.get("videos").service.create(idea.text, TODAY + timedelta(days=5), TODAY)
    await core.events.emit("video.created", video=video, idea_id=idea.id)
    assert ideas.count_open() == 0
    assert await core.get("ideas").morning_section(TODAY) is None
