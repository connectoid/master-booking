"""API кабинета мастера на реальном PostgreSQL (нужен TEST_DATABASE_URL)."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from tests.test_api import TOKEN, auth

MASTER_USER = 900
TZ = ZoneInfo("Asia/Anadyr")


@pytest.fixture
async def cab(migrated_db):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import Settings, get_settings
    from app.db import get_session
    from app.main import app

    engine = create_async_engine(migrated_db.replace("+psycopg", "+asyncpg"), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE masters RESTART IDENTITY CASCADE"))
        await conn.execute(text(
            "INSERT INTO masters (slug, name, timezone, min_lead_min) "
            "VALUES ('anna_nails', 'Анна', 'Asia/Anadyr', 0)"))
        await conn.execute(text(
            "INSERT INTO master_accounts (master_id, channel, external_user_id) "
            f"VALUES (1, 'max', '{MASTER_USER}')"))
        await conn.execute(text(
            "INSERT INTO services (master_id, name, duration_min, price) VALUES (1, 'Маникюр', 60, 1000)"))
        await conn.execute(text(
            "INSERT INTO working_intervals (master_id, weekday, start_time, end_time) "
            "SELECT 1, d, '10:00', '18:00' FROM generate_series(0, 4) d"))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: Settings(max_bot_token=TOKEN)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE masters RESTART IDENTITY CASCADE"))
        await engine.dispose()


M = auth(user_id=MASTER_USER, name="Анна")


async def test_access_only_for_master(cab):
    assert (await cab.get("/api/cabinet/me")).status_code == 401
    r = await cab.get("/api/cabinet/me", headers=auth(user_id=111))
    assert r.status_code == 403 and "мастеру" in r.json()["detail"]
    r = await cab.get("/api/cabinet/me", headers=M)
    assert r.status_code == 200
    me = r.json()
    assert me["slug"] == "anna_nails"
    days = me["schedule"]["days"]
    assert len(days) == 7
    assert days[0]["intervals"] == [{"start": "10:00:00", "end": "18:00:00"}]
    assert days[6]["intervals"] == []
    assert me["settings"]["horizon_days"] == 30
    assert {"id": "Asia/Kamchatka", "title": "Камчатка, Чукотка (МСК+9)"} in me["timezones"]


async def test_put_schedule_and_slots_follow(cab):
    body = {"days": [
        {"weekday": d, "intervals": [{"start": "09:00", "end": "13:00"}, {"start": "14:00", "end": "17:00"}]}
        for d in range(7)
    ]}
    r = await cab.put("/api/cabinet/schedule", json=body, headers=M)
    assert r.status_code == 200, r.text
    me = (await cab.get("/api/cabinet/me", headers=M)).json()
    assert len(me["schedule"]["days"][6]["intervals"]) == 2

    day = dt.datetime.now(TZ).date() + dt.timedelta(days=2)
    r = await cab.get("/api/masters/anna_nails/slots",
                      params={"service_id": 1, "date_from": day.isoformat(), "days": 1})
    times = [s[11:16] for s in r.json()["days"][0]["slots"]]
    assert times[0] == "09:00" and "12:30" not in times and "14:00" in times and times[-1] == "16:00"


@pytest.mark.parametrize("days,needle", [
    ([{"weekday": 0, "intervals": [{"start": "10:00", "end": "14:00"},
                                   {"start": "13:00", "end": "18:00"}]}], "пересекаются"),
    ([{"weekday": 2, "intervals": [{"start": "18:00", "end": "10:00"}]}], "раньше конца"),
    ([{"weekday": 0, "intervals": []}], "хотя бы один рабочий день"),
])
async def test_put_schedule_validation(cab, days, needle):
    r = await cab.put("/api/cabinet/schedule", json={"days": days}, headers=M)
    assert r.status_code == 422 and needle in r.json()["detail"]


async def test_put_schedule_rejects_duplicate_weekday(cab):
    days = [{"weekday": 1, "intervals": [{"start": "10:00", "end": "12:00"}]}] * 2
    r = await cab.put("/api/cabinet/schedule", json={"days": days}, headers=M)
    assert r.status_code == 422


async def test_settings_patch(cab):
    r = await cab.patch("/api/cabinet/settings", headers=M,
                        json={"min_lead_min": 120, "one_booking_per_day": True, "horizon_days": 60})
    assert r.status_code == 200
    assert r.json() == {"name": "Анна", "min_lead_min": 120, "horizon_days": 60,
                        "one_booking_per_day": True, "timezone": "Asia/Anadyr",
                        "slot_step_min": 15, "buffer_min": 0}
    for bad in ({"min_lead_min": 7}, {"horizon_days": 365}, {"timezone": "Mars/Base"},
                {"slot_step_min": 7}, {"buffer_min": 3}, {"name": "а"}, {"name": "   "}):
        assert (await cab.patch("/api/cabinet/settings", headers=M, json=bad)).status_code == 422


async def test_settings_name_step_buffer(cab):
    r = await cab.patch("/api/cabinet/settings", headers=M,
                        json={"name": "  Анна   Петрова ", "slot_step_min": 30, "buffer_min": 30})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["name"] == "Анна Петрова" and got["slot_step_min"] == 30 and got["buffer_min"] == 30
    me = (await cab.get("/api/cabinet/me", headers=M)).json()
    assert me["name"] == "Анна Петрова"
    assert me["step_options"] and me["buffer_options"]

    day = dt.date.fromisoformat(me["today"]) + dt.timedelta(days=1)
    while day.weekday() > 4:
        day += dt.timedelta(days=1)
    params = {"service_id": 1, "date": day.isoformat()}
    slots = (await cab.get("/api/cabinet/free-slots", headers=M, params=params)).json()
    assert [s[11:16] for s in slots[:3]] == ["10:00", "10:30", "11:00"]  # шаг 30 мин

    at = f"{day.isoformat()}T10:00:00{me['utc_offset']}"
    r = await cab.post("/api/cabinet/appointments", headers=M,
                       json={"service_id": 1, "starts_at": at, "client_name": "Ирина"})
    assert r.status_code == 201, r.text
    slots = (await cab.get("/api/cabinet/free-slots", headers=M, params=params)).json()
    assert slots[0][11:16] == "11:30"  # 10:00–11:00 + пауза 30 мин


async def test_overrides_crud_and_conflicts(cab):
    from tests.test_api import in_days

    # клиент записался на день, который мастер потом закроет
    day = in_days(3)
    slot = (await cab.get("/api/masters/anna_nails/slots", params={
        "service_id": 1, "date_from": day.isoformat(), "days": 7})).json()["days"][0]
    r = await cab.post("/api/masters/anna_nails/appointments",
                       json={"service_id": 1, "starts_at": slot["slots"][0]}, headers=auth())
    assert r.status_code == 201
    booked_day = slot["date"]

    r = await cab.put("/api/cabinet/overrides", headers=M,
                      json={"dates": [booked_day], "intervals": []})
    assert r.status_code == 200
    assert [c["client_name"] for c in r.json()] == ["Ольга"]

    other = (dt.date.fromisoformat(booked_day) + dt.timedelta(days=1)).isoformat()
    r = await cab.put("/api/cabinet/overrides", headers=M,
                      json={"dates": [other], "intervals": [{"start": "12:00", "end": "14:00"}]})
    assert r.status_code == 200 and r.json() == []

    got = (await cab.get("/api/cabinet/overrides", headers=M)).json()
    assert got == [
        {"date": booked_day, "intervals": []},
        {"date": other, "intervals": [{"start": "12:00:00", "end": "14:00:00"}]},
    ]
    r = await cab.delete("/api/cabinet/overrides", headers=M, params={"start": booked_day, "end": other})
    assert r.status_code == 204
    assert (await cab.get("/api/cabinet/overrides", headers=M)).json() == []

    past = (dt.datetime.now(TZ).date() - dt.timedelta(days=1)).isoformat()
    r = await cab.put("/api/cabinet/overrides", headers=M, json={"dates": [past], "intervals": []})
    assert r.status_code == 422


# ---------- записи и ручная запись ----------

async def _book_as_client(cab, day_offset=3, user_id=111):
    from tests.test_api import in_days

    day = in_days(day_offset)
    d = (await cab.get("/api/masters/anna_nails/slots", params={
        "service_id": 1, "date_from": day.isoformat(), "days": 7})).json()["days"][0]
    r = await cab.post("/api/masters/anna_nails/appointments",
                       json={"service_id": 1, "starts_at": d["slots"][0], "phone": "89140000000"},
                       headers=auth(user_id=user_id))
    assert r.status_code == 201, r.text
    return r.json()


async def test_list_services_and_appointments(cab):
    services = (await cab.get("/api/cabinet/services", headers=M)).json()
    assert [s["name"] for s in services] == ["Маникюр"]
    booked = await _book_as_client(cab)
    me = (await cab.get("/api/cabinet/me", headers=M)).json()
    r = await cab.get("/api/cabinet/appointments", headers=M,
                      params={"date_from": me["today"], "days": 14})
    items = r.json()
    assert [a["id"] for a in items] == [booked["id"]]
    assert items[0]["client"] == {"id": 1, "name": "Ольга", "phone": "+79140000000"}
    assert items[0]["source"] == "max"
    assert me["utc_offset"] == "+12:00"
    # чужой мастер записи не видит
    assert (await cab.get("/api/cabinet/appointments", headers=auth(user_id=111))).status_code == 403


async def test_free_slots_ignore_min_lead(cab):
    await cab.patch("/api/cabinet/settings", headers=M, json={"min_lead_min": 1440})
    me = (await cab.get("/api/cabinet/me", headers=M)).json()
    day = dt.date.fromisoformat(me["today"]) + dt.timedelta(days=1)
    while day.weekday() > 4:  # ближайший рабочий день (пн–пт)
        day += dt.timedelta(days=1)
    slots = (await cab.get("/api/cabinet/free-slots", headers=M,
                           params={"service_id": 1, "date": day.isoformat()})).json()
    assert slots[0][11:16] == "10:00" and slots[-1][11:16] == "17:00"


async def test_manual_booking_flow(cab):
    me = (await cab.get("/api/cabinet/me", headers=M)).json()
    day = dt.date.fromisoformat(me["today"]) + dt.timedelta(days=5)
    at = f"{day.isoformat()}T20:30:00{me['utc_offset']}"  # вне графика — мастеру можно

    r = await cab.post("/api/cabinet/appointments", headers=M, json={
        "service_id": 1, "starts_at": at, "client_name": "Ирина", "phone": "8 (914) 111-22-33",
        "comment": "по звонку"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["source"] == "manual" and a["client"]["phone"] == "+79141112233"
    assert a["starts_at"][11:16] == "20:30" and a["ends_at"][11:16] == "21:30"

    # тот же телефон — тот же клиент; пересечение — 409
    r = await cab.post("/api/cabinet/appointments", headers=M, json={
        "service_id": 1, "starts_at": at.replace("20:30", "21:00"), "client_name": "Ирина Р.",
        "phone": "+79141112233"})
    assert r.status_code == 409
    r = await cab.post("/api/cabinet/appointments", headers=M, json={
        "service_id": 1, "starts_at": at.replace("20:30", "22:00"), "client_name": "Ирина Р.",
        "phone": "+79141112233"})
    assert r.status_code == 201 and r.json()["client"]["id"] == a["client"]["id"]

    # подсказки по имени и телефону
    hints = (await cab.get("/api/cabinet/clients", headers=M, params={"q": "ири"})).json()
    assert [c["name"] for c in hints] == ["Ирина Р."]
    hints = (await cab.get("/api/cabinet/clients", headers=M, params={"q": "1112233"})).json()
    assert len(hints) == 1

    # запись по client_id, прошлое время, нет имени
    r = await cab.post("/api/cabinet/appointments", headers=M, json={
        "service_id": 1, "starts_at": at.replace("20:30", "08:00"), "client_id": a["client"]["id"]})
    assert r.status_code == 201
    past = f"{(day - dt.timedelta(days=10)).isoformat()}T10:00:00{me['utc_offset']}"
    r = await cab.post("/api/cabinet/appointments", headers=M, json={
        "service_id": 1, "starts_at": past, "client_name": "Кто-то"})
    assert r.status_code == 422
    r = await cab.post("/api/cabinet/appointments", headers=M, json={
        "service_id": 1, "starts_at": at.replace("20:30", "07:00")})
    assert r.status_code == 422


async def test_master_cancel_notifies_client(cab):
    from app.models import Channel
    from app.runtime import runtime
    from tests.test_bot_flow import FakeMessenger

    booked = await _book_as_client(cab)
    fake = FakeMessenger()
    runtime.messengers[Channel.MAX] = fake
    try:
        r = await cab.post(f"/api/cabinet/appointments/{booked['id']}/cancel", headers=M)
        assert r.status_code == 204
        assert any(uid == "111" and "отменил" in text for uid, text, _ in fake.sent)
        r = await cab.post(f"/api/cabinet/appointments/{booked['id']}/cancel", headers=M)
        assert r.status_code == 403
        assert (await cab.post("/api/cabinet/appointments/999999/cancel", headers=M)).status_code == 404
    finally:
        runtime.reset()
    items = (await cab.get("/api/cabinet/appointments", headers=M)).json()
    assert items == []


async def test_appointments_window_limit(cab):
    assert (await cab.get("/api/cabinet/appointments", headers=M, params={"days": 92})).status_code == 200
    assert (await cab.get("/api/cabinet/appointments", headers=M, params={"days": 93})).status_code == 422


async def test_services_crud(cab):
    url = "/api/cabinet/services"
    r = await cab.post(url, headers=M, json={"name": "  Педикюр  с покрытием ", "duration_min": 90,
                                              "price": 2000})
    assert r.status_code == 201, r.text
    ped = r.json()
    assert ped == {"id": ped["id"], "name": "Педикюр с покрытием", "duration_min": 90,
                   "price": 2000, "is_active": True}
    r = await cab.post(url, headers=M, json={"name": "Дизайн", "duration_min": 30})
    assert r.status_code == 201 and r.json()["price"] is None  # по договорённости
    design = r.json()

    for bad in ({"name": "а", "duration_min": 60}, {"name": "Стрижка", "duration_min": 62},
                {"name": "Стрижка", "duration_min": 0}, {"name": "Стрижка", "duration_min": 60, "price": -1}):
        assert (await cab.post(url, headers=M, json=bad)).status_code == 422

    # правка: цена → «по договорённости», длительность, скрытие
    r = await cab.patch(f"{url}/{ped['id']}", headers=M, json={"price": None, "duration_min": 120})
    assert r.status_code == 200 and r.json()["price"] is None and r.json()["duration_min"] == 120
    r = await cab.patch(f"{url}/{design['id']}", headers=M, json={"is_active": False})
    assert r.json()["is_active"] is False
    assert (await cab.patch(f"{url}/{design['id']}", headers=M, json={"name": None})).status_code == 422

    # скрытая не видна клиентам и в ручной записи, но видна в списке кабинета
    public = (await cab.get("/api/masters/anna_nails")).json()
    assert [s["name"] for s in public["services"]] == ["Маникюр", "Педикюр с покрытием"]
    assert [s["name"] for s in (await cab.get(url, headers=M)).json()] == ["Маникюр", "Педикюр с покрытием"]
    full = (await cab.get(url, headers=M, params={"include_hidden": True})).json()
    assert [(s["name"], s["is_active"]) for s in full] == [
        ("Маникюр", True), ("Педикюр с покрытием", True), ("Дизайн", False)]

    # порядок
    ids = [design["id"], ped["id"], 1]
    r = await cab.put(f"{url}/order", headers=M, json={"ids": ids})
    assert r.status_code == 200 and [s["id"] for s in r.json()] == ids
    assert (await cab.put(f"{url}/order", headers=M, json={"ids": [1, ped["id"]]})).status_code == 409
    public = (await cab.get("/api/masters/anna_nails")).json()
    assert [s["name"] for s in public["services"]] == ["Педикюр с покрытием", "Маникюр"]

    # чужой мастер не трогает
    assert (await cab.patch(f"{url}/1", headers=auth(user_id=111), json={"price": 1})).status_code == 403


async def test_delete_service_keeps_appointments(cab):
    booked = await _book_as_client(cab)
    r = await cab.delete("/api/cabinet/services/1", headers=M)
    assert r.status_code == 204
    assert (await cab.delete("/api/cabinet/services/1", headers=M)).status_code == 404
    me = (await cab.get("/api/cabinet/me", headers=M)).json()
    items = (await cab.get("/api/cabinet/appointments", headers=M,
                           params={"date_from": me["today"], "days": 14})).json()
    assert [(a["id"], a["service_name"]) for a in items] == [(booked["id"], "Маникюр")]
