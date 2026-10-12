import datetime as dt

import pytest

from app.bot.parsing import (
    ParseError,
    ParsedService,
    format_schedule,
    parse_duration,
    parse_price,
    parse_schedule,
    parse_services,
)
from app.domain.slugs import slug_base

T = dt.time


@pytest.mark.parametrize("raw,minutes", [
    ("90", 90), ("90 мин", 90), ("45мин", 45), ("1ч", 60), ("1,5 ч", 90), ("1.5ч", 90),
    ("2 часа", 120), ("1:30", 90), ("1 ч 30 мин", 90),
])
def test_parse_duration(raw, minutes):
    assert parse_duration(raw) == minutes


@pytest.mark.parametrize("raw", ["полтора", "0", "3 мин", "13ч", ""])
def test_parse_duration_rejects(raw):
    with pytest.raises(ParseError):
        parse_duration(raw)


@pytest.mark.parametrize("raw,price", [
    ("1500", 1500), ("1 500 ₽", 1500), ("1500р", 1500), ("1500 руб", 1500), ("-", None), ("", None),
])
def test_parse_price(raw, price):
    assert parse_price(raw) == price


def test_parse_services_multiline():
    text = "Маникюр с покрытием, 90, 1500\n\nСнятие; 30; 300\nДизайн, 1ч"
    assert parse_services(text) == [
        ParsedService("Маникюр с покрытием", 90, 1500),
        ParsedService("Снятие", 30, 300),
        ParsedService("Дизайн", 60, None),
    ]


@pytest.mark.parametrize("text", ["", "Маникюр", "Маникюр, долго", "А, 90, 1500, лишнее"])
def test_parse_services_errors(text):
    with pytest.raises(ParseError):
        parse_services(text)


def test_parse_schedule_basic():
    w = parse_schedule("пн-пт 10-19\nсб 11:00-16:00")
    assert w == {d: [(T(10), T(19))] for d in range(5)} | {5: [(T(11), T(16))]}


def test_parse_schedule_breaks_lists_and_overrides():
    w = parse_schedule("Пн, ср 10-14, 15-19\nпн 12.30-18\nвс выходной")
    assert w == {0: [(T(12, 30), T(18))], 2: [(T(10), T(14)), (T(15), T(19))]}


def test_parse_schedule_wraparound_and_dash_variants():
    w = parse_schedule("сб–пн 10 – 18")
    assert sorted(w) == [0, 5, 6]


def test_parse_schedule_until_midnight():
    assert parse_schedule("пт 18-24") == {4: [(T(18), T(23, 59))]}


@pytest.mark.parametrize("text", [
    "", "каждый день 10-19", "пн 19-10", "пн 10-14, 13-18", "пн 25-26", "пн десять",
])
def test_parse_schedule_errors(text):
    with pytest.raises(ParseError):
        parse_schedule(text)


def test_format_schedule_groups_days():
    w = parse_schedule("пн-пт 10-19\nсб 11-16")
    assert format_schedule(w) == "Пн–Пт: 10:00–19:00\nСб: 11:00–16:00\nВс: выходной"


def test_format_schedule_breaks():
    w = parse_schedule("ср 10-14, 15-19")
    assert format_schedule(w) == (
        "Пн–Вт: выходной\nСр: 10:00–14:00, 15:00–19:00\nЧт–Вс: выходной"
    )


@pytest.mark.parametrize("name,slug", [
    ("Анна", "anna"), ("Анна, маникюр", "anna_manikyur"), ("Studio Ёлка", "studio_elka"),
    ("Я", "master"), ("Щедрая Юлия Ивановна-Петрова", "schedraya_yuliya_iva"),
])
def test_slug_base(name, slug):
    assert slug_base(name) == slug
