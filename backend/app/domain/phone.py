"""Нормализация российских телефонов. Чистая функция."""
import re


def normalize_phone(raw: str | None) -> str | None:
    """«8 (914) 123-45-67», «+7 914 1234567», «9141234567» → «+79141234567». Пусто → None."""
    if not raw or not raw.strip():
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    raise ValueError("Неверный номер телефона")
