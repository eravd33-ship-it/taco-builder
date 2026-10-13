from datetime import date

import pytest

from creator_bot.core import ui
from creator_bot.core.dates import parse_date, to_ptb_days, week_start
from creator_bot.storage import Database

TUESDAY = date(2026, 10, 13)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("היום", date(2026, 10, 13)),
        ("מחר", date(2026, 10, 14)),
        ("מחרתיים", date(2026, 10, 15)),
        ("בעוד שבוע", date(2026, 10, 20)),
        ("שבועיים", date(2026, 10, 27)),
        ("5", date(2026, 10, 18)),
        ("בעוד 5 ימים", date(2026, 10, 18)),
        ("יום חמישי", date(2026, 10, 15)),
        ("שלישי", date(2026, 10, 20)),  # today is Tuesday -> next week's
        ("25.10", date(2026, 10, 25)),
        ("25/10/2026", date(2026, 10, 25)),
        ("3.1", date(2027, 1, 3)),  # already passed this year -> next year
        ("1.2.27", date(2027, 2, 1)),
        ("31.2", None),
        ("סתם טקסט", None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text, TUESDAY) == expected


def test_week_start_sunday_and_monday():
    assert week_start(TUESDAY, 6) == date(2026, 10, 11)
    assert week_start(date(2026, 10, 11), 6) == date(2026, 10, 11)
    assert week_start(TUESDAY, 0) == date(2026, 10, 12)


def test_ptb_day_numbers():
    # Python: Monday=0, Sunday=6; python-telegram-bot: Sunday=0
    assert to_ptb_days([6, 0]) == (0, 1)
    assert to_ptb_days([5]) == (6,)


def test_formatting_helpers():
    assert ui.fmt_day(TUESDAY, TUESDAY) == "היום"
    assert ui.fmt_day(date(2026, 10, 14), TUESDAY) == "מחר"
    assert ui.fmt_day(date(2026, 10, 15)) == "יום ה׳ 15.10"
    assert ui.progress_bar(1, 2) == "▓▓▓▓▓░░░░░ 50%"
    assert ui.progress_bar(5, 2).endswith("100%")
    assert ui.days_text(1) == "יום אחד" and ui.days_text(2) == "יומיים" and ui.days_text(4) == "4 ימים"


def test_database_migrations_are_applied_once():
    db = Database()
    steps = ["CREATE TABLE t (a INTEGER);"]
    db.migrate("demo", steps)
    db.migrate("demo", steps)  # no "table already exists" error
    db.migrate("demo", [*steps, "ALTER TABLE t ADD COLUMN b TEXT;"])
    assert db.get("schema.demo") == 2
    db.set("x", {"a": 1})
    assert db.get("x") == {"a": 1}
    assert db.snapshot().startswith(b"SQLite format 3")
