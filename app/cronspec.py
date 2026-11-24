"""
A small cron schedule parser: five fields, the usual crontab syntax.

    minute  hour  day-of-month  month  day-of-week
    0       1     *             *      mon            -> Mondays at 01:00

Each field takes *, a number, a range (1-5), a step (*/15, 1-30/2), a list
(mon,thu) and, for months and weekdays, names (jan, mon). Weekday 0 and 7 are
both Sunday. As in cron, when both day-of-month and day-of-week are limited,
a day matching either one runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

# How far ahead next_after() looks before deciding a schedule never fires
# (e.g. "0 0 31 2 *", February 31st).
SEARCH_DAYS = 366 * 5


@dataclass(frozen=True)
class Schedule:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]        # 0 = Sunday ... 6 = Saturday
    days_limited: bool
    weekdays_limited: bool
    text: str

    def day_matches(self, when: datetime) -> bool:

        if when.month not in self.months:
            return False

        cron_weekday = (when.weekday() + 1) % 7      # Python: Monday = 0
        day_ok = when.day in self.days
        weekday_ok = cron_weekday in self.weekdays

        if self.days_limited and self.weekdays_limited:
            return day_ok or weekday_ok

        return day_ok and weekday_ok

    def matches(self, when: datetime) -> bool:
        return (
            self.day_matches(when)
            and when.hour in self.hours
            and when.minute in self.minutes
        )

    def next_after(self, when: datetime) -> datetime:
        """The first matching minute strictly after `when`."""

        start = (when + timedelta(minutes=1)).replace(second=0, microsecond=0)
        day = start.replace(hour=0, minute=0)

        for _ in range(SEARCH_DAYS):

            if self.day_matches(day):

                for hour in sorted(self.hours):
                    for minute in sorted(self.minutes):
                        candidate = day.replace(hour=hour, minute=minute)
                        if candidate >= start:
                            return candidate

            day += timedelta(days=1)

        raise ValueError(f"'{self.text}' never runs")


def _value(token: str, low: int, high: int, names: list[str] | None, name_base: int) -> int:

    token = token.strip().lower()

    if names and token[:3] in names and token.isalpha():
        return names.index(token[:3]) + name_base

    if not token.isdigit():
        raise ValueError(f"'{token}' is not a number")

    number = int(token)

    if not low <= number <= high:
        raise ValueError(f"{number} is outside {low}-{high}")

    return number


def _field(text: str, low: int, high: int, names: list[str] | None = None, name_base: int = 0) -> tuple[frozenset[int], bool]:
    """The values a field allows, and whether it limits anything (not *)."""

    values: set[int] = set()
    limited = True

    for part in text.split(","):

        part = part.strip()

        if not part:
            raise ValueError(f"empty item in '{text}'")

        step = 1

        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                raise ValueError(f"bad step '/{step_text}'")
            step = int(step_text)

        if part == "*":
            first, last = low, high
            if step == 1:
                limited = False
        elif "-" in part:
            a, b = part.split("-", 1)
            first = _value(a, low, high, names, name_base)
            last = _value(b, low, high, names, name_base)
            if first > last:
                raise ValueError(f"range '{part}' runs backwards")
        else:
            first = _value(part, low, high, names, name_base)
            last = high if step > 1 else first

        values.update(range(first, last + 1, step))

    return frozenset(values), limited


def parse(text: str) -> Schedule:
    """Parse a five-field cron schedule. Raises ValueError with the reason."""

    fields = text.split()

    if len(fields) != 5:
        raise ValueError(
            f"'{text}' needs 5 fields (minute hour day month weekday), e.g. '0 1 * * mon'"
        )

    try:
        minutes, _ = _field(fields[0], 0, 59)
        hours, _ = _field(fields[1], 0, 23)
        days, days_limited = _field(fields[2], 1, 31)
        months, _ = _field(fields[3], 1, 12, MONTHS, 1)
        weekdays, weekdays_limited = _field(fields[4], 0, 7, DAYS, 0)
    except ValueError as exc:
        raise ValueError(f"'{text}': {exc}") from None

    # 7 is Sunday too.
    if 7 in weekdays:
        weekdays = (weekdays - {7}) | {0}

    schedule = Schedule(
        minutes=minutes,
        hours=hours,
        days=days,
        months=months,
        weekdays=frozenset(weekdays),
        days_limited=days_limited,
        weekdays_limited=weekdays_limited,
        text=text,
    )

    # Fail now, not at the first run, for dates that never exist.
    schedule.next_after(datetime(2000, 1, 1))

    return schedule
