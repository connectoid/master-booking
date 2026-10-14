"""QR-код в SVG без внешних библиотек (байтовый режим, уровень коррекции M, версии 1–10).

Нужен для страницы «Создать кабинет»: с компьютера мастер наводит камеру телефона
на код и попадает в бота MAX. До ~210 байт текста — с запасом для ссылки.
Алгоритм — по ISO/IEC 18004 (как в библиотеке Nayuki «QR Code generator»).
"""
from __future__ import annotations

# версия → (EC-кодовых слов на блок, [(блоков, данных в блоке), ...]) для уровня M
_BLOCKS_M = {
    1: (10, [(1, 16)]),
    2: (16, [(1, 28)]),
    3: (26, [(1, 44)]),
    4: (18, [(2, 32)]),
    5: (24, [(2, 43)]),
    6: (16, [(4, 27)]),
    7: (18, [(4, 31)]),
    8: (22, [(2, 38), (2, 39)]),
    9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}
_ECL_M_FORMAT_BITS = 0  # L=1, M=0, Q=3, H=2


# ---------- Рида — Соломона над GF(256), полином 0x11D ----------

def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


# ---------- кодирование ----------

def _data_capacity(version: int) -> int:
    _, groups = _BLOCKS_M[version]
    return sum(n * size for n, size in groups)


def _codewords(data: bytes, version: int) -> list[int]:
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in range(length - 1, -1, -1))

    put(0b0100, 4)                                  # байтовый режим
    put(len(data), 8 if version <= 9 else 16)       # длина
    for b in data:
        put(b, 8)
    capacity = _data_capacity(version) * 8
    put(0, min(4, capacity - len(bits)))            # терминатор
    put(0, (-len(bits)) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    ec_len, groups = _BLOCKS_M[version]
    divisor = _rs_divisor(ec_len)
    blocks, pos = [], 0
    for n, size in groups:
        for _ in range(n):
            chunk = words[pos:pos + size]
            pos += size
            blocks.append((chunk, _rs_remainder(chunk, divisor)))
    out = []
    for i in range(max(len(d) for d, _ in blocks)):  # чередование блоков
        out.extend(d[i] for d, _ in blocks if i < len(d))
    for i in range(ec_len):
        out.extend(e[i] for _, e in blocks)
    return out


class _Matrix:
    def __init__(self, version: int):
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.func = [[False] * self.size for _ in range(self.size)]

    def set_func(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.func[y][x] = True

    def draw_function_patterns(self) -> None:
        n = self.size
        for i in range(n):  # синхронизирующие линии
            self.set_func(6, i, i % 2 == 0)
            self.set_func(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):  # поисковые узоры с отступом
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        d = max(abs(dx), abs(dy))
                        self.set_func(x, y, d not in (2, 4))
        pos = _ALIGN[self.version]  # выравнивающие узоры
        last = len(pos) - 1
        for i, a in enumerate(pos):
            for j, b in enumerate(pos):
                if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_func(a + dx, b + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # резервируем место, перезапишем после выбора маски
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.version << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self.set_func(a, b, bit)
                self.set_func(b, a, bit)

    def draw_format(self, mask: int) -> None:
        data = _ECL_M_FORMAT_BITS << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1  # noqa: E731
        n = self.size
        for i in range(6):
            self.set_func(8, i, bit(i))
        self.set_func(8, 7, bit(6))
        self.set_func(8, 8, bit(7))
        self.set_func(7, 8, bit(8))
        for i in range(9, 15):
            self.set_func(14 - i, 8, bit(i))
        for i in range(8):
            self.set_func(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_func(8, n - 15 + i, bit(i))
        self.set_func(8, n - 8, True)  # тёмный модуль

    def draw_codewords(self, words: list[int]) -> None:
        n, i, total = self.size, 0, len(words) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.func[y][x] and i < total:
                        self.dark[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if self.func[y][x]:
                    continue
                if mask == 0:
                    inv = (x + y) % 2 == 0
                elif mask == 1:
                    inv = y % 2 == 0
                elif mask == 2:
                    inv = x % 3 == 0
                elif mask == 3:
                    inv = (x + y) % 3 == 0
                elif mask == 4:
                    inv = (x // 3 + y // 2) % 2 == 0
                elif mask == 5:
                    inv = x * y % 2 + x * y % 3 == 0
                elif mask == 6:
                    inv = (x * y % 2 + x * y % 3) % 2 == 0
                else:
                    inv = ((x + y) % 2 + x * y % 3) % 2 == 0
                if inv:
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        """Упрощённая оценка из стандарта: длинные ряды, квадраты 2×2, баланс цветов."""
        n, d, score = self.size, self.dark, 0
        lines = [row for row in d] + [[d[y][x] for y in range(n)] for x in range(n)]
        for line in lines:
            run, prev = 0, None
            for v in line:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, prev = 1, v
            if run >= 5:
                score += run - 2
            s = "".join("1" if v else "0" for v in line)
            score += 40 * (s.count("1011101" + "0000") + s.count("0000" + "1011101"))
        for y in range(n - 1):
            for x in range(n - 1):
                if d[y][x] == d[y][x + 1] == d[y + 1][x] == d[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(row) for row in d)
        score += (abs(dark * 20 - n * n * 10) // (n * n)) * 10
        return score


def encode(text: str) -> list[list[bool]]:
    data = text.encode("utf-8")
    for version in range(1, 11):
        header_bits = 4 + (8 if version <= 9 else 16)
        if header_bits + len(data) * 8 <= _data_capacity(version) * 8:
            break
    else:
        raise ValueError("Слишком длинный текст для QR-кода")
    words = _codewords(data, version)
    best, best_score = None, None
    for mask in range(8):
        m = _Matrix(version)
        m.draw_function_patterns()
        m.draw_codewords(words)
        m.apply_mask(mask)
        m.draw_format(mask)
        score = m.penalty()
        if best_score is None or score < best_score:
            best, best_score = m, score
    return best.dark


def svg(text: str, border: int = 4, dark: str = "#2b211d", light: str = "#ffffff") -> str:
    modules = encode(text)
    n = len(modules)
    size = n + border * 2
    path = "".join(
        f"M{x + border},{y + border}h1v1h-1z"
        for y in range(n) for x in range(n) if modules[y][x]
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="{light}"/>'
        f'<path d="{path}" fill="{dark}"/></svg>'
    )
