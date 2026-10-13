"""Date helpers: understanding dates typed in Hebrew, weeks and months."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta

# Python weekday numbers: Monday=0 ... Sunday=6
HEBREW_DAY_NAMES = {"ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5}
RELATIVE_WORDS = {"היום": 0, "מחר": 1, "מחרתיים": 2, "שבוע": 7, "שבועיים": 14, "חודש": 30}

_DAYS_RE = re.compile(r"^(?:בעוד\s+)?(\d{1,3})\s*(?:ימים|יום)?$")
_DATE_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2}|\d{4}))?$")


def parse_date(text: str, today: date) -> date | None:
    """Understand "25.10", "25/10/2026", "מחר", "בעוד 5 ימים", "יום חמישי", "שבוע"."""
    value = " ".join(text.strip().split())
    plain = value.removeprefix("בעוד ").removeprefix("עוד ").strip()
    if plain in RELATIVE_WORDS:
        return today + timedelta(days=RELATIVE_WORDS[plain])

    if match := _DAYS_RE.match(value):
        return today + timedelta(days=int(match.group(1)))

    day_name = value.removeprefix("יום ").strip()
    if day_name in HEBREW_DAY_NAMES:
        ahead = (HEBREW_DAY_NAMES[day_name] - today.weekday()) % 7 or 7
        return today + timedelta(days=ahead)

    if match := _DATE_RE.match(value):
        day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
        try:
            if year:
                full_year = int(year) + 2000 if len(year) == 2 else int(year)
                return date(full_year, month, day)
            result = date(today.year, month, day)
            return result if result >= today else date(today.year + 1, month, day)
        except ValueError:
            return None
    return None


def week_start(day: date, first_weekday: int) -> date:
    return day - timedelta(days=(day.weekday() - first_weekday) % 7)


def month_start(day: date) -> date:
    return day.replace(day=1)


def to_ptb_days(weekdays: Iterable[int]) -> tuple[int, ...]:
    """python-telegram-bot counts days 0=Sunday...6=Saturday; Python uses 0=Monday."""
    return tuple(sorted((weekday + 1) % 7 for weekday in weekdays))
