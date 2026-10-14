"""Веб-версия на реальном PostgreSQL: запись из браузера, напоминания через MAX, кабинет в браузере."""
import datetime as dt

import pytest

from app.models import Channel
from tests.test_api import auth, in_days
from tests.test_bot_flow import FakeMessenger
from tests.test_cabinet import M, cab  # noqa: F401 — фикстура


@pytest.fixture
async def web(cab, migrated_db, monkeypatch):  # noqa: F811
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import get_settings
    from app.runtime import runtime

    monkeypatch.setenv("WEB_SECRET", "test-web-secret")
    get_settings.cache_clear()
    engine = create_async_engine(migrated_db.replace("+psycopg", "+asyncpg"), poolclass=NullPool)
    fake = FakeMessenger()
    runtime.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    runtime.messengers[Channel.MAX] = fake
    runtime.bot_usernames[Channel.MAX] = "id123_bot"
    try:
        yield cab, fake
    finally:
        runtime.reset()
        get_settings.cache_clear()
        await engine.dispose()


async def _session(c):
    token = (await c.post("/api/web/session")).json()["token"]
    return {"Authorization": f"web {token}"}


async def _slot(c, day_offset=3, idx=0):
    """Первое свободное время (после каждой записи — следующее)."""
    d = (await c.get("/api/masters/anna_nails/slots", params={
        "service_id": 1, "date_from": in_days(day_offset).isoformat(), "days": 7})).json()["days"][0]
    return d["slots"][idx]


async def test_web_booking_limits_and_cancel(web):
    c, fake = web
    w = await _session(c)
    url = "/api/masters/anna_nails/appointments"
    r = await c.post(url, headers=w, json={"service_id": 1, "starts_at": await _slot(c), "client_name": "Ира"})
    assert r.status_code == 422 and "телефон" in r.json()["detail"]
    r = await c.post(url, headers=w, json={"service_id": 1, "starts_at": await _slot(c),
                                           "client_name": "Ира", "phone": "8 914 555-00-11"})
    assert r.status_code == 201, r.text
    first = r.json()
    for i in (1, 2):
        r = await c.post(url, headers=w, json={"service_id": 1, "starts_at": await _slot(c),
                                               "client_name": "Ира", "phone": "89145550011"})
        assert r.status_code == 201, r.text
    r = await c.post(url, headers=w, json={"service_id": 1, "starts_at": await _slot(c),
                                           "client_name": "Ира", "phone": "89145550011"})
    assert r.status_code == 403 and "не больше 3" in r.json()["detail"]

    my = (await c.get(f"{url}/my", headers=w)).json()
    assert len(my) == 3
    assert (await c.get(f"{url}/my", headers=await _session(c))).json() == []  # другой браузер
    r = await c.post(f"/api/appointments/{first['id']}/cancel", headers=w)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"

    # подделанный токен и initData «канала web» не принимаются
    assert (await c.get(f"{url}/my", headers={"Authorization": "web 0123456789abcdef." + "0" * 32})).status_code == 401
    assert (await c.get(f"{url}/my", headers=auth(channel="web"))).status_code == 401

    # мастер видит телефон, запись помечена как с сайта
    me = (await c.get("/api/cabinet/me", headers=M)).json()
    appts = (await c.get("/api/cabinet/appointments", headers=M,
                         params={"date_from": me["today"], "days": 14})).json()
    assert {a["source"] for a in appts} == {"web"} and appts[0]["client"]["phone"] == "+79145550011"


async def test_link_max_for_reminders_and_merge(web):
    from sqlalchemy import text

    from app.bot.handlers import process_event
    from app.bot.ui import Event
    from app.runtime import runtime
    from app.workers.reminders import process_due

    c, fake = web
    w = await _session(c)
    r = await c.post("/api/masters/anna_nails/appointments", headers=w, json={
        "service_id": 1, "starts_at": await _slot(c), "client_name": "Ира", "phone": "89145550011"})
    appt = r.json()

    # клиент уже записывался в MAX раньше — после привязки это один клиент
    r = await c.post("/api/masters/anna_nails/appointments", headers=auth(user_id=777, name="Ирина"),
                     json={"service_id": 1, "starts_at": await _slot(c, day_offset=4)})
    assert r.status_code == 201

    link = (await c.get("/api/web/remind-link", headers=w)).json()["url"]
    assert link.startswith("https://max.ru/id123_bot?start=w-")
    code = link.split("start=")[1]
    await process_event(Channel.MAX, Event(kind="start", user_id="777", name="Ирина", payload=code))
    text_, _ = fake.last("777")
    assert text_.startswith("Готово!") and text_.count("• ") == 2

    async with runtime.session_factory() as s:
        clients = (await s.execute(text("SELECT count(*) FROM clients"))).scalar()
        assert clients == 1
        phone = (await s.execute(text("SELECT phone FROM clients"))).scalar()
        assert phone == "+79145550011"
        await s.execute(text("UPDATE reminders SET send_at = now() - interval '1 minute' "
                             f"WHERE appointment_id = {appt['id']} AND kind = 'day_before'"))
        await s.commit()
    assert await process_due() == 1
    text_, kb = fake.last("777")
    assert text_.startswith("Завтра") and kb[0][1].payload == f"c:cancel:{appt['id']}"

    # браузер по-прежнему видит свои записи (и записи из MAX — это теперь один клиент)
    assert len((await c.get("/api/masters/anna_nails/appointments/my", headers=w)).json()) == 2

    # просроченная/поддельная ссылка
    await process_event(Channel.MAX, Event(kind="start", user_id="778", payload="w-" + "0" * 16 + "-1-" + "0" * 12))
    assert "устарела" in fake.last("778")[0]


async def test_master_web_login(web):
    c, fake = web
    r = await c.post("/api/cabinet/web-login", headers=M)
    assert r.status_code == 200 and r.json()["sent"] is True
    url = r.json()["url"]
    assert "/cabinet?login=" in url and url in fake.last("900")[0]
    code = url.split("login=")[1]

    assert (await c.post("/api/web/master-login", json={"code": code + "0"})).status_code == 401
    token = (await c.post("/api/web/master-login", json={"code": code})).json()["token"]
    h = {"Authorization": f"master {token}"}
    me = await c.get("/api/cabinet/me", headers=h)
    assert me.status_code == 200 and me.json()["slug"] == "anna_nails"
    assert (await c.get("/api/cabinet/services", headers=h)).status_code == 200
    assert (await c.get("/api/cabinet/me", headers={"Authorization": "master 1.9999999999." + "0" * 32})).status_code == 401
    # бот: кнопка в меню
    from app.bot.handlers import process_event
    from app.bot.ui import Event
    await process_event(Channel.MAX, Event(kind="callback", user_id="900", payload="m:weblogin", callback_id="cb"))
    assert "/cabinet?login=" in fake.last("900")[0]


async def test_pages(web):
    c, _ = web
    for path in ("/anna_nails", "/cabinet", "/any_slug_123"):
        r = await c.get(path)
        assert r.status_code == 200 and '<base href="/app/">' in r.text, path
    assert (await c.get("/Bad-Slug")).status_code == 404
    r = await c.get("/")
    assert r.status_code == 200 and "Окошко" in r.text and "{{" not in r.text
    assert 'href="https://max.ru/id123_bot"' in r.text
    r = await c.get("/privacy")
    assert r.status_code == 200 and "152-ФЗ" in r.text and "{{" not in r.text
    assert (await c.get("/site/site.css")).status_code == 200
    r = await c.get("/start")
    assert r.status_code == 200 and "Создайте кабинет" in r.text and "https://max.ru/id123_bot" in r.text
    r = await c.get("/qr/max-bot.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert (await c.get("/health")).json() == {"status": "ok"}
    assert (await c.get("/api/web/config")).json() == {"max_bot_url": "https://max.ru/id123_bot"}
