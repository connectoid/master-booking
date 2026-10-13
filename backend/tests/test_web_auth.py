"""Подписи веб-версии: токены и коды без БД."""
from app.channels import web_auth
from app.config import Settings

S = Settings(web_secret="secret-1")
OTHER = Settings(web_secret="secret-2")


def test_client_token():
    token = web_auth.new_client_token(S)
    wid = web_auth.verify_client_token(S, token)
    assert wid and len(wid) == 16 and token.startswith(wid)
    assert web_auth.verify_client_token(OTHER, token) is None
    assert web_auth.verify_client_token(S, token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert web_auth.verify_client_token(S, "") is None
    assert web_auth.new_client_token(S) != token


def test_link_code_roundtrip_and_expiry():
    wid = "0123456789abcdef"
    code = web_auth.link_code(S, wid, now=1_000_000)
    assert web_auth.is_link_code(code) and len(code) <= 64 and code == code.lower()
    assert web_auth.parse_link_code(S, code, now=1_000_000) == wid
    assert web_auth.parse_link_code(S, code, now=1_000_000 + web_auth.LINK_TTL_SEC + 1) is None
    assert web_auth.parse_link_code(OTHER, code, now=1_000_000) is None
    assert web_auth.parse_link_code(S, code.replace(wid, "f" * 16), now=1_000_000) is None
    assert not web_auth.is_link_code("anna_123")


def test_master_login_code_and_token():
    code = web_auth.login_code(S, 42, now=1_000_000)
    assert web_auth.verify_login_code(S, code, now=1_000_000 + 599) == 42
    assert web_auth.verify_login_code(S, code, now=1_000_000 + 601) is None
    assert web_auth.verify_login_code(OTHER, code, now=1_000_000) is None

    token = web_auth.master_token(S, 42, now=1_000_000)
    assert web_auth.verify_master_token(S, token, now=1_000_000 + 3600) == 42
    assert web_auth.verify_master_token(S, token, now=1_000_000 + 31 * 24 * 3600) is None
    assert web_auth.verify_master_token(S, token.replace("42.", "43.", 1), now=1_000_000) is None
    # код входа нельзя выдать за токен и наоборот
    assert web_auth.verify_master_token(S, code, now=1_000_000) is None
    assert web_auth.verify_login_code(S, token, now=1_000_000) is None


def test_secret_falls_back_to_bot_token():
    a = Settings(max_bot_token="t1")
    b = Settings(max_bot_token="t2")
    token = web_auth.new_client_token(a)
    assert web_auth.verify_client_token(a, token) and web_auth.verify_client_token(b, token) is None
