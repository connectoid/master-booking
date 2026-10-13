"""Разбор того, что мастер пишет боту: услуги и недельный график. Чистые функции."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
DAY_TITLES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DAY_OFF_WORDS = {"выходной", "выходные", "нет", "-", "—"}

TimeRange = tuple[dt.time, dt.time]
Weekly = dict[int, list[TimeRange]]


class ParseError(ValueError):
    """Ошибка ввода; текст показывается мастеру."""


@dataclass(frozen=True)
class ParsedService:
    name: str
    duration_min: int
    price: int | None


# ---------- длительность и цена ----------

def parse_duration(raw: str) -> int:
    """«90», «90 мин», «1ч», «1,5 ч», «1:30», «1 ч 30 мин» → минуты."""
    s = raw.strip().lower().replace(",", ".")
    if m := re.fullmatch(r"(\d+)\s*(?:м|мин|минут|минуты)?\.?", s):
        minutes = int(m[1])
    elif m := re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:ч|час|часа|часов)\.?", s):
        minutes = round(float(m[1]) * 60)
    elif m := re.fullmatch(r"(\d+)\s*(?:ч|час|часа)\.?\s*(\d+)\s*(?:м|мин|минут)?\.?", s):
        minutes = int(m[1]) * 60 + int(m[2])
    elif m := re.fullmatch(r"(\d+):(\d{2})", s):
        minutes = int(m[1]) * 60 + int(m[2])
    else:
        raise ParseError(f"Не понял длительность «{raw.strip()}». Пример: 90 или 1,5ч")
    if not 5 <= minutes <= 720:
        raise ParseError("Длительность должна быть от 5 минут до 12 часов")
    return minutes


def parse_price(raw: str) -> int | None:
    s = raw.strip().lower()
    if s in {"", "-", "—", "договорная", "по договорённости", "по договоренности"}:
        return None
    digits = re.sub(r"[\s₽]|руб\.?|р\.?$", "", s)
    if not digits.isdigit():
        raise ParseError(f"Не понял цену «{raw.strip()}». Пример: 1500")
    return int(digits)


def parse_services(text: str) -> list[ParsedService]:
    """Каждая строка: «Название, длительность, цена». Цену можно не указывать."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"[;,]", line)]
        if len(parts) < 2 or not parts[0]:
            raise ParseError(
                f"Строка «{line}»: нужно название и длительность через запятую. "
                "Пример: Маникюр, 90, 1500"
            )
        if len(parts) > 3:
            raise ParseError(f"Строка «{line}»: слишком много частей. Пример: Маникюр, 90, 1500")
        name = parts[0][:120]
        duration = parse_duration(parts[1])
        price = parse_price(parts[2]) if len(parts) == 3 else None
        items.append(ParsedService(name, duration, price))
    if not items:
        raise ParseError("Пустое сообщение. Пример: Маникюр, 90, 1500")
    return items


# ---------- график ----------

def _parse_time(raw: str) -> dt.time:
    s = raw.strip().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", s)
    if not m:
        raise ParseError(f"Не понял время «{raw.strip()}». Пример: 10:00")
    h, mnt = int(m[1]), int(m[2] or 0)
    if h == 24 and mnt == 0:
        return dt.time(23, 59)
    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        raise ParseError(f"Нет такого времени: «{raw.strip()}»")
    return dt.time(h, mnt)


def _parse_days(raw: str) -> list[int]:
    days: list[int] = []
    for token in re.split(r"\s*,\s*", raw.strip().lower()):
        token = token.replace("–", "-").replace("—", "-")
        if "-" in token:
            a, b = (t.strip()[:2] for t in token.split("-", 1))
            if a not in DAYS or b not in DAYS:
                raise ParseError(f"Не понял дни «{token}». Пример: пн-пт")
            i, j = DAYS.index(a), DAYS.index(b)
            days.extend(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
        else:
            if token[:2] not in DAYS:
                raise ParseError(f"Не понял день «{token}». Дни: пн, вт, ср, чт, пт, сб, вс")
            days.append(DAYS.index(token[:2]))
    return days


def check_ranges(ranges: list[TimeRange], where: str = "") -> list[TimeRange]:
    """Проверка интервалов дня: начало раньше конца, без пересечений. Возвращает отсортированные."""
    ranges = sorted(ranges)
    for start, end in ranges:
        if start >= end:
            raise ParseError(f"{where}начало {start:%H:%M} должно быть раньше конца {end:%H:%M}")
    for (_, e1), (s2, _) in zip(ranges, ranges[1:], strict=False):
        if s2 < e1:
            raise ParseError(f"{where}интервалы пересекаются")
    return ranges


def parse_ranges(times_raw: str, line: str = "") -> list[TimeRange]:
    """«10-14, 15-19» → [(10:00, 14:00), (15:00, 19:00)] без пересечений."""
    ranges: list[TimeRange] = []
    for part in re.split(r"\s*,\s*", times_raw.strip()):
        bounds = re.split(r"\s*[-–—]\s*", part)
        if len(bounds) != 2:
            raise ParseError(f"Не понял интервал «{part}». Пример: 10:00-19:00")
        start, end = _parse_time(bounds[0]), _parse_time(bounds[1])
        if start >= end:
            raise ParseError(f"Интервал «{part}»: начало должно быть раньше конца")
        ranges.append((start, end))
    ranges.sort()
    for (_, e1), (s2, _) in zip(ranges, ranges[1:], strict=False):
        if s2 < e1:
            raise ParseError(f"Интервалы в строке «{line or times_raw}» пересекаются")
    return ranges


def parse_schedule(text: str) -> Weekly:
    """Строки вида «пн-пт 10-19», «сб 11:00-16:00», «пн,ср 10-14, 15-19», «вс выходной».

    Несколько интервалов через запятую — перерыв между ними.
    Более поздняя строка для того же дня заменяет предыдущую.
    Дни, которые не упомянуты, — выходные.
    """
    weekly: Weekly = {}
    seen_any = False
    for line in text.splitlines():
        line = line.strip().lower()
        if not line:
            continue
        m = re.match(r"^([а-яё,\s\-–—]+?)\s+(\d.*|выходн\w*|нет|-|—)$", line)
        if not m:
            raise ParseError(f"Строка «{line}»: нужны дни и время. Пример: пн-пт 10-19")
        days = _parse_days(m[1])
        times_raw = m[2].strip()
        seen_any = True
        if times_raw in DAY_OFF_WORDS or times_raw.startswith("выходн"):
            for d in days:
                weekly[d] = []
            continue
        ranges = parse_ranges(times_raw, line)
        for d in days:
            weekly[d] = list(ranges)
    if not seen_any:
        raise ParseError("Пустое сообщение. Пример: пн-пт 10-19")
    return {d: r for d, r in weekly.items() if r}


def _fmt_ranges(ranges: list[TimeRange]) -> str:
    return ", ".join(f"{s:%H:%M}–{e:%H:%M}" for s, e in ranges)


def format_schedule(weekly: Weekly) -> str:
    """Человекочитаемый график; подряд идущие дни с одинаковыми часами склеиваются."""
    lines = []
    d = 0
    while d < 7:
        ranges = sorted(weekly.get(d, []))
        j = d
        while j + 1 < 7 and sorted(weekly.get(j + 1, [])) == ranges:
            j += 1
        days = DAY_TITLES[d] if d == j else f"{DAY_TITLES[d]}–{DAY_TITLES[j]}"
        lines.append(f"{days}: {_fmt_ranges(ranges) if ranges else 'выходной'}")
        d = j + 1
    return "\n".join(lines)



# ---------- даты: выходные, отпуск, график на неделю ----------

MAX_RANGE_DAYS = 92
DateOverrides = dict[dt.date, list[TimeRange]]  # пустой список — выходной
_DATE = r"\d{1,2}\.\d{1,2}(?:\.\d{2,4})?"


def parse_date(raw: str, today: dt.date) -> dt.date:
    """«25.10», «25.10.2026», «25.10.26». Без года — ближайшая такая дата не раньше сегодня."""
    parts = raw.strip().split(".")
    try:
        day, month = int(parts[0]), int(parts[1])
        if len(parts) == 3:
            year = int(parts[2])
            year = year + 2000 if year < 100 else year
            result = dt.date(year, month, day)
        else:
            result = dt.date(today.year, month, day)
            if result < today:
                result = dt.date(today.year + 1, month, day)
    except (ValueError, IndexError):
        raise ParseError(f"Не понял дату «{raw.strip()}». Пример: 25.10") from None
    if result < today:
        raise ParseError(f"Дата {result:%d.%m.%Y} уже прошла")
    return result


def parse_date_range(raw: str, today: dt.date) -> tuple[dt.date, dt.date]:
    """«25.10» или «1.11-10.11» → (начало, конец) включительно."""
    m = re.fullmatch(rf"\s*({_DATE})\s*(?:[-–—]\s*({_DATE}))?\s*", raw)
    if not m:
        raise ParseError(f"Не понял даты «{raw.strip()}». Пример: 25.10 или 1.11-10.11")
    start = parse_date(m[1], today)
    end = parse_date(m[2], start) if m[2] else start
    if end < start:
        raise ParseError("Конец периода раньше начала")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise ParseError(f"Слишком длинный период — не больше {MAX_RANGE_DAYS} дней за раз")
    return start, end


def days_between(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def parse_exceptions(text: str, today: dt.date) -> DateOverrides:
    """Строки «25.10» / «1.11-10.11 отпуск» (выходные) или «26.10 12-16» (другие часы)."""
    result: DateOverrides = {}
    for line in text.splitlines():
        line = line.strip().lower()
        if not line:
            continue
        m = re.match(rf"^({_DATE}(?:\s*[-–—]\s*{_DATE})?)\s*(.*)$", line)
        if not m:
            raise ParseError(f"Строка «{line}»: начните с даты. Пример: 25.10 выходной")
        start, end = parse_date_range(m[1], today)
        rest = m[2].strip(" —-:")
        if not rest or rest.startswith(("выходн", "отпуск", "нет", "не работаю", "больничн")):
            ranges: list[TimeRange] = []
        else:
            ranges = parse_ranges(rest, line)
        for d in days_between(start, end):
            result[d] = list(ranges)
    if not result:
        raise ParseError("Пустое сообщение. Пример: 25.10 выходной")
    return result


def week_overrides(start: dt.date, end: dt.date, weekly: Weekly) -> DateOverrides:
    """Недельный график, применённый к конкретным датам (не указанные дни — выходные)."""
    return {d: list(weekly.get(d.weekday(), [])) for d in days_between(start, end)}


MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _fmt_date(d: dt.date) -> str:
    return f"{d.day} {MONTHS_SHORT[d.month - 1]}"


def group_overrides(overrides: DateOverrides) -> list[tuple[dt.date, dt.date, list[TimeRange]]]:
    """Склеивает подряд идущие даты с одинаковыми часами: [(начало, конец, часы)]."""
    groups: list[tuple[dt.date, dt.date, list[TimeRange]]] = []
    for d in sorted(overrides):
        ranges = sorted(overrides[d])
        if groups and groups[-1][1] + dt.timedelta(days=1) == d and groups[-1][2] == ranges:
            groups[-1] = (groups[-1][0], d, ranges)
        else:
            groups.append((d, d, ranges))
    return groups


def format_period(start: dt.date, end: dt.date) -> str:
    return _fmt_date(start) if start == end else f"{_fmt_date(start)} – {_fmt_date(end)}"


def format_overrides(overrides: DateOverrides) -> str:
    lines = []
    for start, end, ranges in group_overrides(overrides):
        what = _fmt_ranges(ranges) if ranges else "выходной"
        lines.append(f"{format_period(start, end)}: {what}")
    return "\n".join(lines)
