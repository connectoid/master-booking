import datetime as dt

import pytest

from app.bot.parsing import (
    ParseError,
    format_overrides,
    parse_date,
    parse_date_range,
    parse_exceptions,
    parse_schedule,
    week_overrides,
)

T = dt.time
TODAY = dt.date(2026, 10, 13)  # вторник


@pytest.mark.parametrize("raw,expected", [
    ("25.10", dt.date(2026, 10, 25)),
    ("13.10", dt.date(2026, 10, 13)),          # сегодня — можно
    ("5.01", dt.date(2027, 1, 5)),             # без года — ближайшая будущая
    ("25.10.2026", dt.date(2026, 10, 25)),
    ("25.10.27", dt.date(2027, 10, 25)),
])
def test_parse_date(raw, expected):
    assert parse_date(raw, TODAY) == expected


@pytest.mark.parametrize("raw", ["32.10", "1.13", "12.10.2026", "завтра", "10"])
def test_parse_date_errors(raw):
    with pytest.raises(ParseError):
        parse_date(raw, TODAY)


def test_parse_date_range_across_new_year():
    assert parse_date_range("28.12 - 5.01", TODAY) == (dt.date(2026, 12, 28), dt.date(2027, 1, 5))


def test_parse_date_range_limits():
    with pytest.raises(ParseError):
        parse_date_range("1.11-1.03", TODAY)  # слишком длинно
    with pytest.raises(ParseError):
        parse_date_range("10.11.2026-1.11.2026", TODAY)  # конец раньше начала


def test_parse_exceptions_mixed():
    o = parse_exceptions("25.10\n1.11-3.11 отпуск\n26.10 12-16\n27.10 — выходной", TODAY)
    assert o[dt.date(2026, 10, 25)] == []
    assert o[dt.date(2026, 11, 2)] == []
    assert o[dt.date(2026, 10, 26)] == [(T(12), T(16))]
    assert o[dt.date(2026, 10, 27)] == []
    assert len(o) == 6


@pytest.mark.parametrize("text", ["", "отпуск 1.11", "25.10 полдня", "25.10 16-12"])
def test_parse_exceptions_errors(text):
    with pytest.raises(ParseError):
        parse_exceptions(text, TODAY)


def test_week_overrides_and_format():
    weekly = parse_schedule("пн-пт 12-20\nсб 10-14")
    o = week_overrides(dt.date(2026, 11, 2), dt.date(2026, 11, 8), weekly)  # пн–вс
    assert o[dt.date(2026, 11, 2)] == [(T(12), T(20))]
    assert o[dt.date(2026, 11, 7)] == [(T(10), T(14))]
    assert o[dt.date(2026, 11, 8)] == []
    assert format_overrides(o) == (
        "2 ноя – 6 ноя: 12:00–20:00\n7 ноя: 10:00–14:00\n8 ноя: выходной"
    )
