"""Проверка данных запуска Mini App (initData) для MAX и Telegram.

Алгоритм у обеих платформ одинаковый:
  1. разобрать query-строку, убрать параметр `hash`;
  2. отсортировать пары по ключу и склеить строкой "key=value" через "\\n";
  3. secret_key = HMAC_SHA256(key="WebAppData", msg=BOT_TOKEN);
  4. hash должен совпасть с hex(HMAC_SHA256(key=secret_key, msg=data_check_string)).

MAX: https://dev.max.ru/docs/webapps/validation
Telegram: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    """initData поддельные, повреждённые или устаревшие."""


@dataclass(frozen=True)
class WebAppUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str | None = None
    language_code: str | None = None


@dataclass(frozen=True)
class InitData:
    user: WebAppUser | None
    auth_date: dt.datetime
    start_param: str | None
    chat: dict[str, Any] | None
    query_id: str | None
    raw: dict[str, str]


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def sign(fields: dict[str, str], bot_token: str) -> str:
    """Подпись набора полей (без hash). Нужна для тестов и локальной отладки."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    return hmac.new(
        _secret_key(bot_token), data_check_string.encode(), hashlib.sha256
    ).hexdigest()


def verify_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age: dt.timedelta | None = dt.timedelta(hours=24),
    now: dt.datetime | None = None,
) -> InitData:
    if not bot_token:
        raise InitDataError("bot token is not configured")
    if not init_data:
        raise InitDataError("empty init data")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as e:
        raise InitDataError("malformed init data") from e

    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise InitDataError(f"duplicate field: {key}")
        fields[key] = value

    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataError("hash is missing")
    expected = sign(fields, bot_token).encode()
    if not hmac.compare_digest(expected, received_hash.lower().encode()):
        raise InitDataError("bad signature")

    try:
        auth_date = dt.datetime.fromtimestamp(int(fields["auth_date"]), tz=dt.UTC)
    except (KeyError, ValueError) as e:
        raise InitDataError("auth_date is missing or invalid") from e

    if max_age is not None:
        now = now or dt.datetime.now(dt.UTC)
        if now - auth_date > max_age:
            raise InitDataError("init data is expired")

    return InitData(
        user=_parse_user(fields.get("user")),
        auth_date=auth_date,
        start_param=fields.get("start_param") or None,
        chat=_parse_json(fields.get("chat"), "chat"),
        query_id=fields.get("query_id"),
        raw=fields,
    )


def _parse_json(value: str | None, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        data = json.loads(value)
    except json.JSONDecodeError as e:
        raise InitDataError(f"{name} is not valid JSON") from e
    if not isinstance(data, dict):
        raise InitDataError(f"{name} must be an object")
    return data


def _parse_user(value: str | None) -> WebAppUser | None:
    data = _parse_json(value, "user")
    if data is None:
        return None
    try:
        user_id = int(data["id"])
    except (KeyError, TypeError, ValueError) as e:
        raise InitDataError("user.id is missing") from e
    return WebAppUser(
        id=user_id,
        first_name=data.get("first_name") or "",
        last_name=data.get("last_name") or "",
        username=data.get("username"),
        language_code=data.get("language_code"),
    )
