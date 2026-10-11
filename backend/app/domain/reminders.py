"""Когда отправлять напоминания о записи. Чистая логика, без БД."""
from __future__ import annotations

import datetime as dt

DAY_BEFORE = "day_before"
TWO_HOURS = "two_hours"

OFFSETS: dict[str, dt.timedelta] = {
    DAY_BEFORE: dt.timedelta(hours=24),
    TWO_HOURS: dt.timedelta(hours=2),
}

# Не шлём напоминание, если до него осталось меньше этого: клиент только что записался
MIN_GAP_AFTER_BOOKING = dt.timedelta(minutes=30)


def plan_reminders(starts_at: dt.datetime, now: dt.datetime) -> list[tuple[str, dt.datetime]]:
    """Список (вид, время отправки) для записи на starts_at, созданной в момент now.

    Напоминание пропускается, если его время уже прошло или наступит меньше чем через
    MIN_GAP_AFTER_BOOKING (записался за 23 часа — «за сутки» не шлём, только «за 2 часа»).
    """
    if starts_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("datetimes must be timezone-aware")
    plan = []
    for kind, offset in OFFSETS.items():
        send_at = starts_at - offset
        if send_at - now >= MIN_GAP_AFTER_BOOKING:
            plan.append((kind, send_at))
    return plan
