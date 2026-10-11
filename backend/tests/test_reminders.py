import datetime as dt

import pytest

from app.domain.reminders import DAY_BEFORE, TWO_HOURS, plan_reminders

NOW = dt.datetime(2026, 10, 12, 10, 0, tzinfo=dt.UTC)
H = dt.timedelta(hours=1)


def kinds(plan):
    return [k for k, _ in plan]


def test_far_booking_gets_both_reminders():
    starts = NOW + 72 * H
    plan = plan_reminders(starts, NOW)
    assert plan == [(DAY_BEFORE, starts - 24 * H), (TWO_HOURS, starts - 2 * H)]


def test_booking_for_tomorrow_within_24h_gets_only_two_hours():
    assert kinds(plan_reminders(NOW + 20 * H, NOW)) == [TWO_HOURS]


def test_day_before_skipped_if_it_would_fire_right_after_booking():
    # «за сутки» наступит через 10 минут после записи — не шлём
    starts = NOW + 24 * H + dt.timedelta(minutes=10)
    assert kinds(plan_reminders(starts, NOW)) == [TWO_HOURS]


def test_booking_soon_gets_no_reminders():
    assert plan_reminders(NOW + 2 * H, NOW) == []
    assert plan_reminders(NOW + H, NOW) == []


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError):
        plan_reminders(dt.datetime(2026, 10, 15, 10), NOW)
