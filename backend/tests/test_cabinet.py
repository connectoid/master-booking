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
    assert r.json() == {"min_lead_min": 120, "horizon_days": 60, "one_booking_per_day": True,
                        "timezone": "Asia/Anadyr"}
    for bad in ({"min_lead_min": 7}, {"horizon_days": 365}, {"timezone": "Mars/Base"}):
        assert (await cab.patch("/api/cabinet/settings", headers=M, json=bad)).status_code == 422


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
