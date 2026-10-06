import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.domain.slots import SlotRules, available_slots

TZ = ZoneInfo("Asia/Anadyr")  # UTC+12
MON = dt.date(2026, 10, 12)   # понедельник
T = dt.time
M = dt.timedelta(minutes=1)


def local(day: dt.date, h: int, m: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, T(h, m), tzinfo=TZ)


def hm(slots: list[dt.datetime]) -> list[str]:
    return [s.strftime("%H:%M") for s in slots]


def run(*, weekly=None, overrides=None, busy=(), duration=60, step=30, lead=0, buffer=0,
        now=None, day_from=MON, day_to=MON):
    return available_slots(
        day_from=day_from,
        day_to=day_to,
        tz=TZ,
        weekly=weekly if weekly is not None else {0: [(T(10), T(13))]},
        overrides=overrides or {},
        busy=list(busy),
        rules=SlotRules(duration=duration * M, step=step * M, min_lead=lead * M,
                        buffer=buffer * M),
        now=now or local(MON - dt.timedelta(days=1), 9),
    )


def test_basic_grid_fits_inside_interval():
    assert hm(run()[MON]) == ["10:00", "10:30", "11:00", "11:30", "12:00"]


def test_break_splits_day_into_two_intervals():
    res = run(weekly={0: [(T(14), T(16)), (T(10), T(12))]})
    assert hm(res[MON]) == ["10:00", "10:30", "11:00", "14:00", "14:30", "15:00"]


def test_busy_appointment_blocks_overlapping_slots_but_touching_is_ok():
    res = run(busy=[(local(MON, 11), local(MON, 12))])
    assert hm(res[MON]) == ["10:00", "12:00"]


def test_buffer_between_clients():
    res = run(busy=[(local(MON, 11), local(MON, 12))], buffer=15)
    # 10:00–11:00 + 15 мин пересекается с 11:00; 12:00 < 12:00+15 — тоже нельзя
    assert MON not in res


def test_buffer_allows_slot_after_gap():
    res = run(weekly={0: [(T(10), T(14))]}, busy=[(local(MON, 11), local(MON, 12))],
              buffer=15, step=15)
    assert hm(res[MON]) == ["12:15", "12:30", "12:45", "13:00"]


def test_min_lead_time_and_past_slots_are_hidden():
    res = run(now=local(MON, 10, 10), lead=60)
    assert hm(res[MON]) == ["11:30", "12:00"]


def test_day_off_override_and_custom_hours_override():
    tue = MON + dt.timedelta(days=1)
    res = run(
        weekly={0: [(T(10), T(13))], 1: [(T(10), T(13))]},
        overrides={MON: [], tue: [(T(15), T(17))]},
        day_to=tue,
    )
    assert MON not in res
    assert hm(res[tue]) == ["15:00", "15:30", "16:00"]


def test_days_without_schedule_are_skipped():
    res = run(day_to=MON + dt.timedelta(days=6))
    assert list(res) == [MON]


def test_result_is_in_master_timezone_and_correct_instant():
    slot = run()[MON][0]
    assert slot.tzinfo == TZ
    assert slot.astimezone(dt.UTC) == dt.datetime(2026, 10, 11, 22, 0, tzinfo=dt.UTC)


def test_busy_in_other_timezone_is_compared_by_instant():
    # 11:00 по Анадырю = 02:00 по Москве (UTC+3) того же дня
    msk = ZoneInfo("Europe/Moscow")
    busy = (dt.datetime(2026, 10, 12, 2, 0, tzinfo=msk), dt.datetime(2026, 10, 12, 3, 0, tzinfo=msk))
    assert hm(run(busy=[busy])[MON]) == ["10:00", "12:00"]


def test_dst_transition_uses_real_durations():
    # В Европе/Берлине 2026-03-29 02:00 -> 03:00. Интервал 01:00–04:00 длится 2 реальных часа.
    berlin = ZoneInfo("Europe/Berlin")
    day = dt.date(2026, 3, 29)  # воскресенье
    res = available_slots(
        day_from=day, day_to=day, tz=berlin,
        weekly={6: [(T(1), T(4))]}, overrides={}, busy=[],
        rules=SlotRules(duration=60 * M, step=60 * M, min_lead=0 * M),
        now=dt.datetime(2026, 3, 1, tzinfo=dt.UTC),
    )
    assert hm(res[day]) == ["01:00", "03:00"]


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        run(now=dt.datetime(2026, 10, 12, 9))
