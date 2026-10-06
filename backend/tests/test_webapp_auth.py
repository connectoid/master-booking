import datetime as dt
import json
from urllib.parse import urlencode

import pytest

from app.channels.webapp_auth import InitDataError, sign, verify_init_data

TOKEN = "test-bot-token"
NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)


def make(fields: dict[str, str], token: str = TOKEN) -> str:
    return urlencode({**fields, "hash": sign(fields, token)})


def base_fields(**over):
    fields = {
        "auth_date": str(int(NOW.timestamp()) - 60),
        "query_id": "AAH",
        "user": json.dumps({"id": 123456, "first_name": "Ольга", "language_code": "ru"},
                           ensure_ascii=False),
        "start_param": "anna_nails",
    }
    fields.update(over)
    return fields


def test_valid_init_data():
    data = verify_init_data(make(base_fields()), TOKEN, now=NOW)
    assert data.user.id == 123456
    assert data.user.first_name == "Ольга"
    assert data.start_param == "anna_nails"


def test_known_vector_matches_reference_algorithm():
    # Независимый пересчёт по документации: key=WebAppData, msg=token
    import hashlib
    import hmac
    fields = {"auth_date": "1700000000", "user": '{"id":1}'}
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, b'auth_date=1700000000\nuser={"id":1}', hashlib.sha256).hexdigest()
    assert sign(fields, TOKEN) == expected


def test_wrong_token_rejected():
    with pytest.raises(InitDataError, match="signature"):
        verify_init_data(make(base_fields(), token="other"), TOKEN, now=NOW)


def test_tampered_field_rejected():
    raw = make(base_fields()).replace("anna_nails", "evil_master")
    with pytest.raises(InitDataError, match="signature"):
        verify_init_data(raw, TOKEN, now=NOW)


def test_missing_hash_rejected():
    with pytest.raises(InitDataError, match="hash"):
        verify_init_data(urlencode(base_fields()), TOKEN, now=NOW)


def test_expired_rejected_and_max_age_can_be_disabled():
    raw = make(base_fields(auth_date=str(int(NOW.timestamp()) - 2 * 86400)))
    with pytest.raises(InitDataError, match="expired"):
        verify_init_data(raw, TOKEN, now=NOW)
    assert verify_init_data(raw, TOKEN, now=NOW, max_age=None).user.id == 123456


def test_duplicate_field_rejected():
    raw = make(base_fields()) + "&start_param=x"
    with pytest.raises(InitDataError, match="duplicate"):
        verify_init_data(raw, TOKEN, now=NOW)


def test_non_ascii_hash_does_not_crash():
    raw = urlencode({**base_fields(), "hash": "хэш"})
    with pytest.raises(InitDataError):
        verify_init_data(raw, TOKEN, now=NOW)


@pytest.mark.parametrize("raw", ["", "%%%", "a=1&&b"])
def test_garbage_rejected(raw):
    with pytest.raises(InitDataError):
        verify_init_data(raw, TOKEN, now=NOW)


def test_empty_token_rejected():
    with pytest.raises(InitDataError, match="token"):
        verify_init_data(make(base_fields()), "", now=NOW)
