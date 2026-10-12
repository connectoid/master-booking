"""Короткий адрес мастера (slug) из имени. Чистая функция."""
import re

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
    strict=True,
))


def slug_base(name: str) -> str:
    """«Анна, маникюр» → «anna_manikyur» (не длиннее 20 символов)."""
    s = "".join(_TRANSLIT.get(ch, ch) for ch in name.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:20].strip("_")
    return s if len(s) >= 3 else "master"
