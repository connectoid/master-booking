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
        ranges: list[TimeRange] = []
        for part in re.split(r"\s*,\s*", times_raw):
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
                raise ParseError(f"Интервалы в строке «{line}» пересекаются")
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
