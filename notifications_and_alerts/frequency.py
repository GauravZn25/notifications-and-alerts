"""How often a reminder repeats — and the arithmetic of its next occurrence.

    Frequency.once(at)                                   one time
    Frequency.every(hours=6)                             every N minutes / hours / days
    Frequency.daily(at="09:00")                          every day (or every N days) at a time
    Frequency.weekly(days=["mon", "thu"], at="10:00")    on weekdays, every N weeks
    Frequency.monthly(day=1, at="09:00")                 a day of the month (31 → the last day)
    Frequency.before(deadline, [timedelta(days=3), timedelta(days=1), timedelta(hours=2)])
                                                         at fixed offsets before a deadline
    Frequency.at_times([t1, t2, …])                      at exact instants

Every kind takes `until=` (no occurrence after it), `max_runs=` (stop after N sends)
and `timezone=` (for the wall-clock kinds; default Asia/Kolkata). Instants are
timezone-aware; a naive datetime is read in the frequency's time zone.

MISSED OCCURRENCES. If the worker was down, a reminder that is due is sent ONCE and
then jumps to its next occurrence in the future — it does not fire a burst to catch
up (`next_after(now)` skips past `now`).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

Kind = Literal["once", "interval", "daily", "weekly", "monthly", "times"]

_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DEFAULT_TZ = "Asia/Kolkata"


class Frequency(BaseModel):
    kind: Kind
    #: The first occurrence is not before this (default: when scheduled).
    start_at: datetime | None = None
    #: daily / weekly / monthly: every N days / weeks / months.
    step: int = 1
    #: interval: minutes between sends.
    interval_minutes: int | None = None
    #: Wall-clock time for daily / weekly / monthly.
    at: time | None = None
    #: weekly: 0 = Monday … 6 = Sunday.
    weekdays: list[int] = Field(default_factory=list)
    #: monthly: 1–31; a month without that day uses its last day.
    day_of_month: int | None = None
    #: times / before: the exact instants.
    times: list[datetime] = Field(default_factory=list)
    timezone: str = DEFAULT_TZ
    until: datetime | None = None
    max_runs: int | None = None

    # --- constructors -------------------------------------------------------------------
    @classmethod
    def once(cls, at: datetime | None = None, *, timezone: str = DEFAULT_TZ) -> "Frequency":
        return cls(kind="once", start_at=at, timezone=timezone, max_runs=1)

    @classmethod
    def every(cls, *, minutes: int = 0, hours: int = 0, days: int = 0,
              start_at: datetime | None = None, until: datetime | None = None,
              max_runs: int | None = None, timezone: str = DEFAULT_TZ) -> "Frequency":
        total = minutes + hours * 60 + days * 1440
        return cls(kind="interval", interval_minutes=total, start_at=start_at, until=until,
                   max_runs=max_runs, timezone=timezone)

    @classmethod
    def daily(cls, at: str | time = "09:00", *, every: int = 1, start_at: datetime | None = None,
              until: datetime | None = None, max_runs: int | None = None,
              timezone: str = DEFAULT_TZ) -> "Frequency":
        return cls(kind="daily", at=_time(at), step=every, start_at=start_at, until=until,
                   max_runs=max_runs, timezone=timezone)

    @classmethod
    def weekly(cls, days: list[str | int] | str = "mon", at: str | time = "09:00", *,
               every: int = 1, start_at: datetime | None = None, until: datetime | None = None,
               max_runs: int | None = None, timezone: str = DEFAULT_TZ) -> "Frequency":
        if isinstance(days, (str, int)):
            days = [days]
        return cls(kind="weekly", weekdays=[_weekday(d) for d in days], at=_time(at), step=every,
                   start_at=start_at, until=until, max_runs=max_runs, timezone=timezone)

    @classmethod
    def monthly(cls, day: int = 1, at: str | time = "09:00", *, every: int = 1,
                start_at: datetime | None = None, until: datetime | None = None,
                max_runs: int | None = None, timezone: str = DEFAULT_TZ) -> "Frequency":
        return cls(kind="monthly", day_of_month=day, at=_time(at), step=every, start_at=start_at,
                   until=until, max_runs=max_runs, timezone=timezone)

    @classmethod
    def before(cls, deadline: datetime, offsets: list[timedelta], *,
               timezone: str = DEFAULT_TZ) -> "Frequency":
        """At each offset before `deadline` — e.g. 3 days, 1 day and 2 hours before a
        reply is due. Offsets already past when it is scheduled are skipped."""
        deadline = _aware(deadline, timezone)
        return cls(kind="times", times=sorted(deadline - o for o in offsets), timezone=timezone)

    @classmethod
    def at_times(cls, instants: list[datetime], *, timezone: str = DEFAULT_TZ) -> "Frequency":
        return cls(kind="times", times=sorted(_aware(t, timezone) for t in instants),
                   timezone=timezone)

    # --- validation ---------------------------------------------------------------------
    @field_validator("timezone")
    @classmethod
    def _zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:  # noqa: BLE001 — ZoneInfoNotFoundError / ValueError
            raise ValueError(f"{value!r} is not a time zone (e.g. Asia/Kolkata)") from exc
        return value

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, value: list[int]) -> list[int]:
        return sorted(set(_weekday(v) for v in value))

    @model_validator(mode="after")
    def _shape(self):
        if self.step < 1:
            raise ValueError("step (every N) must be at least 1")
        if self.kind == "interval" and not (self.interval_minutes and self.interval_minutes > 0):
            raise ValueError("an interval needs minutes, hours or days greater than zero")
        if self.kind in ("daily", "weekly", "monthly") and self.at is None:
            self.at = time(9, 0)
        if self.kind == "weekly" and not self.weekdays:
            raise ValueError("a weekly reminder needs at least one weekday")
        if self.kind == "monthly" and not (self.day_of_month and 1 <= self.day_of_month <= 31):
            raise ValueError("a monthly reminder needs day_of_month between 1 and 31")
        if self.kind == "times" and not self.times:
            raise ValueError("give at least one instant")
        if self.max_runs is not None and self.max_runs < 1:
            raise ValueError("max_runs must be at least 1")
        tz = self.timezone
        self.start_at = _aware(self.start_at, tz) if self.start_at else None
        self.until = _aware(self.until, tz) if self.until else None
        self.times = sorted(_aware(t, tz) for t in self.times)
        return self

    # --- the arithmetic -----------------------------------------------------------------
    def first(self, now: datetime) -> datetime | None:
        """The first occurrence at or after `now` (and `start_at`)."""
        now = _aware(now, self.timezone)
        floor = max(now, self.start_at) if self.start_at else now
        if self.kind in ("once", "interval"):
            return self._bounded(floor, 0)
        if self.kind == "times":
            return self._bounded(next((t for t in self.times if t >= now), None), 0)
        return self._bounded(self._slot_at_or_after(floor), 0)

    def next_after(self, previous: datetime, runs_done: int, *, now: datetime | None = None
                   ) -> datetime | None:
        """The occurrence after `previous`, given `runs_done` sends so far; with `now`,
        the first such occurrence after `now` (missed ones are skipped)."""
        previous = _aware(previous, self.timezone)
        candidate = self._step(previous)
        if now is not None:
            now = _aware(now, self.timezone)
            for _ in range(100_000):  # a bound, not a policy: an interval of a minute for years
                if candidate is None or candidate > now:
                    break
                candidate = self._step(candidate)
        return self._bounded(candidate, runs_done)

    def _step(self, previous: datetime) -> datetime | None:
        if self.kind == "once":
            return None
        if self.kind == "interval":
            return previous + timedelta(minutes=self.interval_minutes or 0)
        if self.kind == "times":
            return next((t for t in self.times if t > previous), None)
        tz = ZoneInfo(self.timezone)
        local = previous.astimezone(tz)
        if self.kind == "daily":
            return _at(local.date() + timedelta(days=self.step), self.at, tz)
        if self.kind == "weekly":
            return self._next_weekly(local)
        return self._month_slot(local.date(), self.step)

    def _slot_at_or_after(self, floor: datetime) -> datetime:
        tz = ZoneInfo(self.timezone)
        local = floor.astimezone(tz)
        if self.kind == "daily":
            today = _at(local.date(), self.at, tz)
            return today if today >= floor else _at(local.date() + timedelta(days=1), self.at, tz)
        if self.kind == "weekly":
            for offset in range(0, 8):
                day = local.date() + timedelta(days=offset)
                slot = _at(day, self.at, tz)
                if day.weekday() in self.weekdays and slot >= floor:
                    return slot
        this_month = self._month_slot(local.date(), 0)
        return this_month if this_month >= floor else self._month_slot(local.date(), 1)

    def _next_weekly(self, local: datetime) -> datetime:
        tz = ZoneInfo(self.timezone)
        anchor = (self.start_at or local).astimezone(tz).date()
        anchor_week = anchor - timedelta(days=anchor.weekday())
        day = local.date()
        for _ in range(7 * self.step + 7):
            day += timedelta(days=1)
            week = day - timedelta(days=day.weekday())
            in_cycle = ((week - anchor_week).days // 7) % self.step == 0
            if in_cycle and day.weekday() in self.weekdays:
                return _at(day, self.at, tz)
        raise AssertionError("unreachable: a weekly cycle always has a day")  # pragma: no cover

    def _month_slot(self, day: date, months_ahead: int) -> datetime:
        tz = ZoneInfo(self.timezone)
        month_index = day.year * 12 + (day.month - 1) + months_ahead
        year, month = divmod(month_index, 12)
        month += 1
        last = calendar.monthrange(year, month)[1]
        return _at(date(year, month, min(self.day_of_month or 1, last)), self.at, tz)

    def _bounded(self, when: datetime | None, runs_done: int) -> datetime | None:
        if when is None:
            return None
        if self.max_runs is not None and runs_done >= self.max_runs:
            return None
        if self.until is not None and when > self.until:
            return None
        return when.astimezone(dt_timezone.utc)

    def describe(self) -> str:
        """Plain words, for a list of reminders: "daily at 09:00 (Asia/Kolkata)"."""
        at = self.at.strftime("%H:%M") if self.at else ""
        names = [k for k, v in _DAYS.items() if v in self.weekdays]
        text = {
            "once": f"once at {self.start_at:%d %b %Y %H:%M}" if self.start_at else "once",
            "interval": f"every {self.interval_minutes} min",
            "daily": f"every {self.step} days at {at}" if self.step > 1 else f"daily at {at}",
            "weekly": f"weekly on {', '.join(names)} at {at}"
                      + (f", every {self.step} weeks" if self.step > 1 else ""),
            "monthly": f"monthly on day {self.day_of_month} at {at}",
            "times": f"at {len(self.times)} set time(s)",
        }[self.kind]
        return f"{text} ({self.timezone})"


# --- helpers ----------------------------------------------------------------------------
def _time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    try:
        hour, minute = (int(x) for x in str(value).split(":")[:2])
        return time(hour, minute)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{value!r} is not a time like 09:00") from exc


def _weekday(value: str | int) -> int:
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    key = str(value).strip().lower()[:3]
    if key not in _DAYS:
        raise ValueError(f"{value!r} is not a weekday (mon … sun)")
    return _DAYS[key]


def _aware(value: datetime, tz: str) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(tz))


def _at(day: date, at: time | None, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, at or time(9, 0), tzinfo=tz)
