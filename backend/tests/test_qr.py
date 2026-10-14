"""QR-код: структура матрицы; если есть OpenCV — ещё и распознавание."""
from app.domain import qr


def _finder_ok(m, x0, y0):
    for dy in range(7):
        for dx in range(7):
            d = max(abs(dx - 3), abs(dy - 3))
            if m[y0 + dy][x0 + dx] != (d != 2):
                return False
    return True


def test_structure_and_versions():
    for text, size in (("https://max.ru/id123_bot", 25), ("x" * 100, 41), ("x" * 200, 57)):
        m = qr.encode(text)
        n = len(m)
        assert n == size and all(len(row) == n for row in m)
        assert _finder_ok(m, 0, 0) and _finder_ok(m, n - 7, 0) and _finder_ok(m, 0, n - 7)
        assert all(m[6][x] == (x % 2 == 0) for x in range(8, n - 8))  # синхронизация
        assert m[n - 8][8]  # тёмный модуль


def test_too_long():
    try:
        qr.encode("x" * 300)
    except ValueError:
        return
    raise AssertionError("ожидали ValueError")


def test_svg():
    s = qr.svg("https://max.ru/id123_bot")
    assert s.startswith("<svg") and 'viewBox="0 0 33 33"' in s


def test_decodes_with_opencv():
    try:
        import cv2
        import numpy as np
    except ImportError:
        return  # на сервере OpenCV нет — достаточно структурных проверок
    det = cv2.QRCodeDetector()
    for text in ("https://max.ru/id4101234567_bot", "https://okoshko.website/anna_manikyur_123"):
        m = qr.encode(text)
        n, s, b = len(m), 8, 4
        img = np.full(((n + 2 * b) * s,) * 2, 255, np.uint8)
        for y in range(n):
            for x in range(n):
                if m[y][x]:
                    img[(y + b) * s:(y + b + 1) * s, (x + b) * s:(x + b + 1) * s] = 0
        assert det.detectAndDecode(img)[0] == text
