from datetime import date, timedelta

from creator_bot.modules.videos import VideosModule, plan_steps

from .conftest import TODAY


def test_plan_counts_back_from_publish_date():
    plan = plan_steps(TODAY + timedelta(days=7), TODAY)
    assert [(step.key, due) for step, due in plan] == [
        ("film", date(2026, 10, 16)),
        ("edit", date(2026, 10, 18)),
        ("upload", date(2026, 10, 20)),
    ]


def test_plan_never_schedules_in_the_past():
    plan = plan_steps(TODAY + timedelta(days=1), TODAY)
    assert [due for _, due in plan] == [TODAY, TODAY, TODAY + timedelta(days=1)]


def test_pipeline_from_filming_to_published(make_core):
    service = make_core([VideosModule]).get("videos").service
    video = service.create("ביקורת מצלמה", TODAY + timedelta(days=7), TODAY)
    assert video.current.key == "film"
    assert service.due(TODAY) == []
    assert [s.key for _, s in service.upcoming(TODAY)] == ["film"]

    _, step, next_step = service.complete_step(video.id, "film", TODAY)
    assert (step.key, next_step.key) == ("film", "edit")
    assert service.complete_step(video.id, "film", TODAY) is None  # double tap does not skip editing

    service.complete_step(video.id, "edit", TODAY)
    video, step, next_step = service.complete_step(video.id, "upload", TODAY)
    assert next_step is None and video.published_on == TODAY
    assert service.active() == []
    assert service.published_between(TODAY, TODAY) == 1


def test_overdue_steps_show_up_and_snooze_pushes_later_steps(make_core):
    service = make_core([VideosModule]).get("videos").service
    video = service.create("ולוג", TODAY + timedelta(days=1), TODAY)  # film & edit due today
    assert [s.key for _, s in service.due(TODAY + timedelta(days=3))] == ["film"]

    later = TODAY + timedelta(days=3)
    video = service.snooze(video.id, "film", later)
    assert [s.due for s in video.steps] == [later + timedelta(days=1)] * 3
    assert service.due(later) == []
    assert service.scheduled_between(later, later + timedelta(days=1)) == 1


async def test_digest_lists_due_tasks_with_buttons(make_core):
    core = make_core([VideosModule])
    core.get("videos").service.create("סרטון בדיקה", TODAY, TODAY)
    section = await core.get("videos").morning_section(TODAY)
    assert "לצלם" in section.text and "סרטון בדיקה" in section.text
    assert section.open_count == 1
    assert section.buttons[0][0].callback_data == "videos:done:1:film"
    assert section.buttons[0][1].callback_data == "videos:snooze:1:film"

    late = await core.get("videos").evening_section(TODAY + timedelta(days=2))
    assert "באיחור של יומיים" in late.text
