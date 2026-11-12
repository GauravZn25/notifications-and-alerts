from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from notifications_and_alerts import Frequency

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def ist(*args) -> datetime:
    return datetime(*args, tzinfo=IST)


NOW = ist(2026, 11, 11, 10, 0)  # a Wednesday


def test_once_is_the_given_instant_then_nothing():
    f = Frequency.once(ist(2026, 11, 12, 9, 0))
    first = f.first(NOW)
    assert first == ist(2026, 11, 12, 9, 0)
    assert f.next_after(first, 1) is None


def test_once_with_no_instant_is_now():
    assert Frequency.once().first(NOW) == NOW


def test_interval_steps_by_its_minutes():
    f = Frequency.every(hours=6)
    first = f.first(NOW)
    assert first == NOW
    assert f.next_after(first, 1) == NOW + timedelta(hours=6)


def test_daily_today_if_still_ahead_else_tomorrow():
    assert Frequency.daily(at="11:00").first(NOW) == ist(2026, 11, 11, 11, 0)
    assert Frequency.daily(at="09:00").first(NOW) == ist(2026, 11, 12, 9, 0)
    f = Frequency.daily(at="09:00", every=2)
    assert f.next_after(ist(2026, 11, 12, 9, 0), 1) == ist(2026, 11, 14, 9, 0)


def test_weekly_on_given_days():
    f = Frequency.weekly(days=["mon", "thu"], at="10:30")
    first = f.first(NOW)                                  # Wed 10:00 → Thu 10:30
    assert first == ist(2026, 11, 12, 10, 30)
    second = f.next_after(first, 1)                       # → Mon 16 Nov
    assert second == ist(2026, 11, 16, 10, 30)
    assert f.next_after(second, 2) == ist(2026, 11, 19, 10, 30)


def test_weekly_every_other_week():
    f = Frequency.weekly(days="mon", at="09:00", every=2, start_at=ist(2026, 11, 16, 0, 0))
    first = f.first(NOW)
    assert first == ist(2026, 11, 16, 9, 0)
    assert f.next_after(first, 1) == ist(2026, 11, 30, 9, 0)


def test_monthly_uses_the_last_day_when_the_month_is_short():
    f = Frequency.monthly(day=31, at="09:00", start_at=ist(2026, 11, 1))
    first = f.first(NOW)
    assert first == ist(2026, 11, 30, 9, 0)               # November has 30 days
    assert f.next_after(first, 1) == ist(2026, 12, 31, 9, 0)
    assert f.next_after(ist(2027, 1, 31, 9, 0), 3) == ist(2027, 2, 28, 9, 0)


def test_before_a_deadline_skips_offsets_already_past():
    deadline = ist(2026, 11, 13, 17, 0)
    f = Frequency.before(deadline, [timedelta(days=3), timedelta(days=1), timedelta(hours=2)])
    first = f.first(NOW)                                  # 3 days before is already past
    assert first == ist(2026, 11, 12, 17, 0)
    assert f.next_after(first, 1) == ist(2026, 11, 13, 15, 0)
    assert f.next_after(ist(2026, 11, 13, 15, 0), 2) is None


def test_max_runs_and_until_stop_it():
    f = Frequency.daily(at="09:00", max_runs=2)
    assert f.next_after(ist(2026, 11, 12, 9, 0), 1) is not None
    assert f.next_after(ist(2026, 11, 13, 9, 0), 2) is None
    g = Frequency.daily(at="09:00", until=ist(2026, 11, 13, 12, 0))
    assert g.next_after(ist(2026, 11, 13, 9, 0), 1) is None


def test_missed_occurrences_are_skipped_not_replayed():
    f = Frequency.every(hours=1)
    previous = ist(2026, 11, 11, 1, 0)                     # the worker was down since 01:00
    nxt = f.next_after(previous, 1, now=NOW)
    assert nxt == ist(2026, 11, 11, 11, 0)


def test_results_are_utc_and_survive_json():
    f = Frequency.weekly(days=["fri"], at="18:15", timezone="Europe/London")
    again = Frequency.model_validate(f.model_dump(mode="json"))
    assert again == f
    assert f.first(NOW).tzinfo == UTC


@pytest.mark.parametrize("bad", [
    lambda: Frequency.every(minutes=0),
    lambda: Frequency.weekly(days=[]),
    lambda: Frequency.weekly(days=["funday"]),
    lambda: Frequency.monthly(day=32),
    lambda: Frequency.daily(at="25:99"),
    lambda: Frequency.daily(timezone="Mars/Olympus"),
])
def test_impossible_frequencies_are_refused(bad):
    with pytest.raises(ValueError):
        bad()


def test_describe_in_plain_words():
    assert Frequency.daily(at="09:00").describe() == "daily at 09:00 (Asia/Kolkata)"
    assert "mon, thu" in Frequency.weekly(days=["thu", "mon"]).describe()
