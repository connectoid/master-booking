"""Вход из обычного браузера — без мессенджера.

Всё подписано HMAC-SHA256 секретом сервера (WEB_SECRET), в БД ничего не хранится:

* токен клиента «<wid>.<подпись>» — анонимный посетитель, выдаётся при первой записи
  и живёт в localStorage браузера; wid — его external_user_id в канале web;
* код привязки «w-<wid>-<срок>-<подпись>» — параметр ссылки на бота MAX: клиент из браузера
  открывает её, и напоминания начинают приходить в MAX;
* код входа мастера «<id>-<срок>-<подпись>» — одноразовая по смыслу ссылка из бота (10 минут);
* токен мастера «<id>.<срок>.<подпись>» — сессия кабинета в браузере.

Только строчные латинские буквы, цифры, «-» и «.» — ссылки не ломаются при пересылке.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time

from app.config import Settings

LINK_TTL_SEC = 3 * 24 * 3600
LOGIN_TTL_SEC = 10 * 60


def _secret(settings: Settings) -> bytes:
    if settings.web_secret:
        return settings.web_secret.encode()
    return hashlib.sha256(b"okoshko-web:" + settings.max_bot_token.encode()).digest()


def _sig(settings: Settings, kind: str, payload: str, size: int) -> str:
    mac = hmac.new(_secret(settings), f"{kind}:{payload}".encode(), hashlib.sha256)
    return mac.hexdigest()[:size]


def _check(settings: Settings, kind: str, payload: str, sig: str, size: int) -> bool:
    return len(sig) == size and hmac.compare_digest(_sig(settings, kind, payload, size), sig)


def _b36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


def _now(now: float | None) -> int:
    return int(now if now is not None else time.time())


# ---------- клиент ----------

def new_client_token(settings: Settings) -> str:
    wid = secrets.token_hex(8)
    return f"{wid}.{_sig(settings, 'c', wid, 32)}"


def verify_client_token(settings: Settings, token: str) -> str | None:
    """Токен клиента → wid; подделка → None."""
    m = re.fullmatch(r"([0-9a-f]{16})\.([0-9a-f]{32})", token or "")
    if not m or not _check(settings, "c", m[1], m[2], 32):
        return None
    return m[1]


def link_code(settings: Settings, wid: str, now: float | None = None) -> str:
    exp = _b36(_now(now) + LINK_TTL_SEC)
    return f"w-{wid}-{exp}-{_sig(settings, 'l', f'{wid}-{exp}', 12)}"


def parse_link_code(settings: Settings, payload: str, now: float | None = None) -> str | None:
    """Параметр ссылки на бота → wid; чужой, поддельный или просроченный → None."""
    m = re.fullmatch(r"w-([0-9a-f]{16})-([0-9a-z]{1,8})-([0-9a-f]{12})", payload or "")
    if not m or not _check(settings, "l", f"{m[1]}-{m[2]}", m[3], 12):
        return None
    if int(m[2], 36) < _now(now):
        return None
    return m[1]


def is_link_code(payload: str) -> bool:
    return (payload or "").startswith("w-")  # slug мастера не содержит «-»


# ---------- мастер ----------

def login_code(settings: Settings, master_id: int, now: float | None = None) -> str:
    payload = f"{_b36(master_id)}-{_b36(_now(now) + LOGIN_TTL_SEC)}"
    return f"{payload}-{_sig(settings, 'ml', payload, 16)}"


def verify_login_code(settings: Settings, code: str, now: float | None = None) -> int | None:
    m = re.fullmatch(r"([0-9a-z]{1,10})-([0-9a-z]{1,8})-([0-9a-f]{16})", code or "")
    if not m or not _check(settings, "ml", f"{m[1]}-{m[2]}", m[3], 16):
        return None
    if int(m[2], 36) < _now(now):
        return None
    return int(m[1], 36)


def master_token(settings: Settings, master_id: int, now: float | None = None) -> str:
    exp = _now(now) + settings.web_master_session_days * 24 * 3600
    payload = f"{master_id}.{exp}"
    return f"{payload}.{_sig(settings, 'mt', payload, 32)}"


def verify_master_token(settings: Settings, token: str, now: float | None = None) -> int | None:
    m = re.fullmatch(r"(\d{1,12})\.(\d{1,12})\.([0-9a-f]{32})", token or "")
    if not m or not _check(settings, "mt", f"{m[1]}.{m[2]}", m[3], 32):
        return None
    if int(m[2]) < _now(now):
        return None
    return int(m[1])
