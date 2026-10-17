from datetime import date, timedelta

import pytest

from creator_bot.modules.motivation import LAST_ACTIVITY, MotivationModule, parse_number
from creator_bot.modules.motivation_texts import QUOTES
from creator_bot.modules.routines import RoutinesModule
from creator_bot.modules.videos import VideosModule

from .conftest import TODAY

MODULES = [MotivationModule, VideosModule, RoutinesModule]


def publish(core, title, day):
    service = core.get("videos").service
    video = service.create(title, day, day)
    for step in ("film", "edit", "upload"):
        service.complete_step(video.id, step, day)


def test_week_progress_and_streak(make_core):
    core = make_core(MODULES, uploads_per_week=1)
    motivation = core.get("motivation")
    # one upload in each of the two previous weeks
    publish(core, "a", date(2026, 10, 1))
    publish(core, "b", date(2026, 10, 8))
    assert motivation.streak(TODAY) == 2

    progress = motivation.week_progress(TODAY)
    assert (progress.start, progress.end, progress.done) == (date(2026, 10, 11), date(2026, 10, 17), 0)
    publish(core, "c", TODAY)
    assert motivation.week_progress(TODAY).met
    assert motivation.streak(TODAY) == 3


async def test_morning_section_nudges_when_behind(make_core):
    core = make_core(MODULES, uploads_per_week=2)
    section = await core.get("motivation").morning_section(TODAY)
    assert QUOTES[TODAY.toordinal() % len(QUOTES)] in section.text
    assert "0/2" in section.text
    assert "חסרים עוד 2 סרטונים" in section.text and "5 ימים" in section.text

    core.get("videos").service.create("x", TODAY + timedelta(days=2), TODAY)
    core.get("videos").service.create("y", TODAY + timedelta(days=3), TODAY)
    section = await core.get("motivation").morning_section(TODAY)
    assert "בדרך הנכונה" in section.text


async def test_first_day_of_week_includes_last_week_summary(make_core):
    core = make_core(MODULES)
    publish(core, "a", date(2026, 10, 8))
    section = await core.get("motivation").morning_section(date(2026, 10, 11))
    assert "סיכום השבוע שעבר" in section.text and "1/1" in section.text


async def test_evening_footer(make_core, clock):
    core = make_core(MODULES)
    motivation = core.get("motivation")
    assert "משימה אחת" in await motivation.digest_footer("evening", TODAY, 1)
    assert await motivation.digest_footer("evening", TODAY, 0) is None  # nothing done yet, nothing due

    core.db.set(LAST_ACTIVITY, TODAY.isoformat())
    assert await motivation.digest_footer("evening", TODAY, 0)  # praise for finishing everything
    assert await motivation.digest_footer("evening", TODAY + timedelta(days=1), 0) is None
    assert "4 ימים" in await motivation.digest_footer("evening", TODAY + timedelta(days=4), 0)


async def test_events_trigger_praise_and_celebration(make_core):
    core = make_core(MODULES)
    service = core.get("videos").service
    video = service.create("הסרטון הגדול", TODAY + timedelta(days=4), TODAY)

    video, step, next_step = service.complete_step(video.id, "film", TODAY)
    await core.events.emit("video.step_done", video=video, step=step, next_step=next_step)
    assert "הבא בתור" in core.bot.sent[-1] and "עריכה" in core.bot.sent[-1]
    assert core.db.get(LAST_ACTIVITY) == TODAY.isoformat()

    await core.events.emit("video.published", video=video)
    assert "באוויר" in core.bot.sent[-1]


async def test_full_digest_puts_sections_together(make_core):
    core = make_core(MODULES)
    core.get("videos").service.create("בדיקה", TODAY, TODAY)
    core.get("routines").service.add("💬 להגיב לתגובות", {TODAY.weekday()}, TODAY)
    text, markup = await core.digest("morning", TODAY, "HEADER")
    assert text.startswith("HEADER")
    assert "בדיקה" in text and "להגיב לתגובות" in text
    assert len(markup.inline_keyboard) == 2

    evening_text, _ = await core.digest("evening", TODAY, "EVENING")
    assert "נשארו 2 משימות" in evening_text or "עוד 2 משימות" in evening_text


async def test_quiet_evening_sends_nothing(make_core):
    core = make_core(MODULES)
    assert await core.digest("evening", TODAY, "EVENING") == (None, None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("10000", 10_000), ("10,000", 10_000), ("10k", 10_000), ("2.5K", 2_500), ("10 אלף", 10_000), ("abc", None)],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected
