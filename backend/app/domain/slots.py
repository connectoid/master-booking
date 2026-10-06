"""Расчёт свободных слотов для записи.

Чистая функция без БД: на вход — график мастера, исключения и занятые интервалы,
на выход — список времён начала, на которые можно записаться.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

TimeRange = tuple[dt.time, dt.time]
Busy = tuple[dt.datetime, dt.datetime]  # aware datetimes, [start, end)


@dataclass(frozen=True)
class SlotRules:
    duration: dt.timedelta          # длительность услуги
    step: dt.timedelta              # шаг сетки слотов (например, 15 минут)
    min_lead: dt.timedelta          # минимум времени до начала записи
    buffer: dt.timedelta = dt.timedelta(0)  # пауза между клиентами


def working_ranges(
    day: dt.date,
    weekly: Mapping[int, Sequence[TimeRange]],
    overrides: Mapping[dt.date, Sequence[TimeRange]],
) -> list[TimeRange]:
    """Рабочие интервалы на дату. Исключение (даже пустое = выходной) заменяет недельный график."""
    ranges = overrides[day] if day in overrides else weekly.get(day.weekday(), ())
    return sorted(ranges)


def available_slots(
    *,
    day_from: dt.date,
    day_to: dt.date,
    tz: ZoneInfo,
    weekly: Mapping[int, Sequence[TimeRange]],
    overrides: Mapping[dt.date, Sequence[TimeRange]],
    busy: Sequence[Busy],
    rules: SlotRules,
    now: dt.datetime,
) -> dict[dt.date, list[dt.datetime]]:
    """Свободные слоты по дням в диапазоне [day_from, day_to] (локальные даты мастера).

    Слот подходит, если:
      * услуга целиком помещается в рабочий интервал;
      * начало не раньше now + min_lead;
      * [начало, конец + buffer) не пересекается ни с одним занятым [b_start, b_end + buffer).
    Сетка слотов начинается от начала каждого рабочего интервала.
    Возвращает aware datetime в часовом поясе мастера; дни без слотов не включаются.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if rules.duration <= dt.timedelta(0) or rules.step <= dt.timedelta(0):
        raise ValueError("duration and step must be positive")

    earliest = now + rules.min_lead
    busy_sorted = sorted(busy)
    result: dict[dt.date, list[dt.datetime]] = {}

    day = day_from
    while day <= day_to:
        slots: list[dt.datetime] = []
        for start_t, end_t in working_ranges(day, weekly, overrides):
            # Считаем в UTC, чтобы арифметика была абсолютной (корректно и при переходах времени).
            start = dt.datetime.combine(day, start_t, tzinfo=tz).astimezone(dt.UTC)
            end = dt.datetime.combine(day, end_t, tzinfo=tz).astimezone(dt.UTC)
            t = start
            while t + rules.duration <= end:
                if t >= earliest and _is_free(t, t + rules.duration, busy_sorted, rules.buffer):
                    slots.append(t.astimezone(tz))
                t += rules.step
        if slots:
            result[day] = slots
        day += dt.timedelta(days=1)
    return result


def _is_free(
    start: dt.datetime, end: dt.datetime, busy: Sequence[Busy], buffer: dt.timedelta
) -> bool:
    for b_start, b_end in busy:
        if b_start >= end + buffer:
            break  # busy отсортирован — дальше только более поздние
        if start < b_end + buffer and b_start < end + buffer:
            return False
    return True
