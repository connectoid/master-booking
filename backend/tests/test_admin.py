"""Админка на реальном PostgreSQL."""
import datetime as dt

import pytest

from app.models import Channel
from tests.test_api import auth
from tests.test_bot_flow import FakeMessenger
from tests.test_cabinet import M, _book_as_client, cab  # noqa: F401 — фикстура


@pytest.fixture
async def adm(cab, migrated_db, monkeypatch):  # noqa: F811
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.channels import web_auth
    from app.config import get_settings
    from app.runtime import runtime

    monkeypatch.setenv("WEB_SECRET", "test-web-secret")
    monkeypatch.setenv("ADMIN_MAX_IDS", "555, 900")
    get_settings.cache_clear()
    engine = create_async_engine(migrated_db.replace("+psycopg", "+asyncpg"), poolclass=NullPool)
    fake = FakeMessenger()
    runtime.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    runtime.messengers[Channel.MAX] = fake
    runtime.bot_usernames[Channel.MAX] = "id123_bot"
    code = web_auth.admin_login_code(get_settings(), "555")
    token = (await cab.post("/api/web/admin-login", json={"code": code})).json()["token"]
    try:
        yield cab, fake, {"Authorization": f"admin {token}"}
    finally:
        runtime.reset()
        get_settings.cache_clear()
        await engine.dispose()


async def test_admin_auth(adm, monkeypatch):
    from app.channels import web_auth
    from app.config import get_settings

    c, _, h = adm
    assert (await c.get("/api/admin/stats")).status_code == 401
    assert (await c.get("/api/admin/stats", headers=M)).status_code == 401  # мастер — не админ
    assert (await c.get("/api/admin/stats", headers=h)).status_code == 200
    bad = web_auth.admin_login_code(get_settings(), "777")  # не в списке
    assert (await c.post("/api/web/admin-login", json={"code": bad})).status_code == 401
    # убрали ID из .env — токен больше не действует
    monkeypatch.setenv("ADMIN_MAX_IDS", "900")
    assert (await c.get("/api/admin/stats", headers=h)).status_code == 401


async def test_stats_and_masters(adm):
    c, _, h = adm
    rows = (await c.get("/api/admin/masters", headers=h)).json()
    assert [(r["slug"], r["status"], r["services"], r["has_schedule"]) for r in rows] == [
        ("anna_nails", "idle", 1, True)]
    assert "phone" not in str(rows)  # данные клиентов в админку не попадают

    await _book_as_client(c)
    r = (await c.get("/api/admin/masters", headers=h, params={"status": "active"})).json()
    assert len(r) == 1 and r[0]["appointments_30d"] == 1 and r[0]["upcoming"] == 1 and r[0]["clients"] == 1
    assert (await c.get("/api/admin/masters", headers=h, params={"q": "нет такого"})).json() == []
    assert len((await c.get("/api/admin/masters", headers=h, params={"q": "АННА"})).json()) == 1

    s = (await c.get("/api/admin/stats", headers=h)).json()
    assert s["masters_total"] == 1 and s["appointments_30d"] == 1 and s["by_source_30d"] == {"max": 1}
    assert s["masters_active_30d"] == 1 and s["upcoming"] == 1 and len(s["daily"]) == 31
    assert s["daily"][-1]["count"] == 1

    d = (await c.get("/api/admin/masters/1", headers=h)).json()
    assert d["services_list"][0]["name"] == "Маникюр" and "10:00–18:00" in d["schedule"]
    assert (await c.get("/api/admin/masters/99", headers=h)).status_code == 404


async def test_block_extend_note_message(adm):
    c, fake, h = adm
    url = "/api/admin/masters/1"
    r = await c.patch(url, headers=h, json={"blocked": True, "admin_note": "  спам  "})
    assert r.status_code == 200 and r.json()["status"] == "blocked" and r.json()["admin_note"] == "спам"
    r = await c.get("/api/masters/anna_nails")
    assert r.status_code == 404 and "недоступна" in r.json()["detail"]
    assert (await c.get("/api/cabinet/me", headers=M)).status_code == 200  # кабинет работает
    await c.patch(url, headers=h, json={"blocked": False})
    assert (await c.get("/api/masters/anna_nails")).status_code == 200

    first = (await c.post(f"{url}/extend", headers=h, json={"days": 30})).json()["paid_until"]
    second = (await c.post(f"{url}/extend", headers=h, json={"days": 30})).json()["paid_until"]
    d1, d2 = dt.datetime.fromisoformat(first), dt.datetime.fromisoformat(second)
    assert (d2 - d1).days == 30
    r = await c.patch(url, headers=h, json={"paid_until": "2027-01-31"})
    assert r.json()["paid_until"].startswith("2027-01-31T")
    assert (await c.patch(url, headers=h, json={"paid_until": None})).json()["paid_until"] is None

    r = await c.post(f"{url}/message", headers=h, json={"text": "Привет! Как вам сервис?"})
    assert r.status_code == 200 and r.json()["sent"] == 1
    assert "Привет! Как вам сервис?" in fake.last("900")[0]


async def test_bot_commands(adm):
    from app.bot.handlers import process_event
    from app.bot.ui import Event

    _, fake, _ = adm
    await process_event(Channel.MAX, Event(kind="text", user_id="555", text="/admin"))
    text, kb = fake.last("555")
    assert "/admin?login=" in text and kb[0][0].url.startswith("http")
    await process_event(Channel.MAX, Event(kind="text", user_id="111", text="/admin"))
    assert fake.last("111")[0] == "Команда недоступна."
    await process_event(Channel.MAX, Event(kind="text", user_id="111", text="/myid"))
    assert fake.last("111")[0] == "Ваш ID в MAX: 111"
