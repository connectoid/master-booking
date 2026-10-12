"""Тесты API клиента на реальном PostgreSQL (нужен TEST_DATABASE_URL)."""
import datetime as dt
import json
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest

from app.channels.webapp_auth import sign

TOKEN = "test-max-token"
TZ = ZoneInfo("Asia/Anadyr")


def auth(user_id: int = 111, name: str = "Ольга", token: str = TOKEN, channel: str = "max"):
    fields = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False),
    }
    init_data = urlencode({**fields, "hash": sign(fields, token)})
    return {"Authorization": f"tma {init_data}", "X-Channel": channel}


def in_days(n: int) -> dt.date:
    return dt.datetime.now(TZ).date() + dt.timedelta(days=n)


@pytest.fixture
async def ctx(migrated_db):
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
            "INSERT INTO masters (slug, name, timezone, slot_step_min, min_lead_min) "
            "VALUES ('anna_nails', 'Анна', 'Asia/Anadyr', 15, 60)"
        ))
        await conn.execute(text(
            "INSERT INTO services (master_id, name, duration_min, price, position, is_active) VALUES "
            "(1, 'Маникюр', 90, 1500, 1, true),"
            "(1, 'Покрытие', 60, 1000, 0, true),"
            "(1, 'Старая услуга', 30, 500, 2, false)"
        ))
        await conn.execute(text(
            "INSERT INTO working_intervals (master_id, weekday, start_time, end_time) "
            "SELECT 1, d, '10:00', '18:00' FROM generate_series(0, 6) d"
        ))

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: Settings(
        max_bot_token=TOKEN, telegram_bot_token="tg-token"
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, engine
    app.dependency_overrides.clear()
    async with engine.begin() as conn:  # не оставляем данные другим тестам
        await conn.execute(text("TRUNCATE masters RESTART IDENTITY CASCADE"))
    await engine.dispose()


async def scalar(engine, sql: str):
    from sqlalchemy import text

    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).scalar()


async def first_slot(client, day: dt.date, service_id: int = 1) -> str:
    r = await client.get(
        "/api/masters/anna_nails/slots",
        params={"service_id": service_id, "date_from": day.isoformat(), "days": 1},
    )
    assert r.status_code == 200, r.text
    return r.json()["days"][0]["slots"][0]


async def test_master_with_active_services_in_order(ctx):
    client, _ = ctx
    r = await client.get("/api/masters/anna_nails")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Анна" and body["timezone"] == "Asia/Anadyr"
    assert [s["name"] for s in body["services"]] == ["Покрытие", "Маникюр"]


async def test_unknown_master_404(ctx):
    client, _ = ctx
    assert (await client.get("/api/masters/nobody")).status_code == 404


async def test_slots_fit_working_hours(ctx):
    client, _ = ctx
    day = in_days(2)
    r = await client.get(
        "/api/masters/anna_nails/slots",
        params={"service_id": 1, "date_from": day.isoformat(), "days": 3},
    )
    assert r.status_code == 200
    days = r.json()["days"]
    assert [d["date"] for d in days] == [(day + dt.timedelta(days=i)).isoformat() for i in range(3)]
    times = [dt.datetime.fromisoformat(s).astimezone(TZ).strftime("%H:%M") for s in days[0]["slots"]]
    assert times[0] == "10:00" and times[-1] == "16:30"  # 90 мин до 18:00
    assert len(times) == 27  # 10:00..16:30 с шагом 15 минут


async def test_inactive_service_slots_404(ctx):
    client, _ = ctx
    r = await client.get("/api/masters/anna_nails/slots", params={"service_id": 3})
    assert r.status_code == 404


async def test_booking_flow(ctx):
    client, engine = ctx
    slot = await first_slot(client, in_days(3))

    r = await client.post(
        "/api/masters/anna_nails/appointments",
        json={"service_id": 1, "starts_at": slot, "phone": "8 (914) 123-45-67"},
        headers=auth(),
    )
    assert r.status_code == 201, r.text
    appt = r.json()
    assert appt["service_name"] == "Маникюр" and appt["price"] == 1500
    assert appt["status"] == "confirmed"

    # клиент создан с именем из мессенджера и нормализованным телефоном
    assert await scalar(engine, "SELECT name || ' ' || phone FROM clients") == "Ольга +79141234567"
    # запись через 3 дня — оба напоминания
    assert await scalar(engine, "SELECT count(*) FROM reminders WHERE status='pending'") == 2

    # слот пропал из выдачи
    assert await first_slot(client, in_days(3)) != slot

    # второй клиент на то же время — недоступно
    r2 = await client.post(
        "/api/masters/anna_nails/appointments",
        json={"service_id": 1, "starts_at": slot},
        headers=auth(user_id=222, name="Ирина"),
    )
    assert r2.status_code == 422

    # «мои записи» видит только владелец
    mine = await client.get("/api/masters/anna_nails/appointments/my", headers=auth())
    assert [a["id"] for a in mine.json()] == [appt["id"]]
    other = await client.get("/api/masters/anna_nails/appointments/my", headers=auth(user_id=222))
    assert other.json() == []

    # чужую запись отменить нельзя
    r3 = await client.post(f"/api/appointments/{appt['id']}/cancel", headers=auth(user_id=222))
    assert r3.status_code == 404

    # отмена владельцем: статус, напоминания, слот снова свободен
    r4 = await client.post(f"/api/appointments/{appt['id']}/cancel", headers=auth())
    assert r4.status_code == 200 and r4.json()["status"] == "cancelled"
    assert await scalar(engine, "SELECT count(*) FROM reminders WHERE status='pending'") == 0
    assert await first_slot(client, in_days(3)) == slot

    # повторная отмена — уже нельзя
    r5 = await client.post(f"/api/appointments/{appt['id']}/cancel", headers=auth())
    assert r5.status_code == 403


async def test_same_user_reuses_client(ctx):
    client, engine = ctx
    day = in_days(4)
    s1 = await first_slot(client, day, service_id=2)
    r1 = await client.post("/api/masters/anna_nails/appointments",
                           json={"service_id": 2, "starts_at": s1}, headers=auth())
    assert r1.status_code == 201
    s2 = await first_slot(client, day, service_id=2)
    r2 = await client.post("/api/masters/anna_nails/appointments",
                           json={"service_id": 2, "starts_at": s2}, headers=auth())
    assert r2.status_code == 201
    assert await scalar(engine, "SELECT count(*) FROM clients") == 1


async def test_off_grid_and_past_times_rejected(ctx):
    client, _ = ctx
    day = in_days(3)
    off_grid = dt.datetime.combine(day, dt.time(10, 7), tzinfo=TZ).isoformat()
    r = await client.post("/api/masters/anna_nails/appointments",
                          json={"service_id": 1, "starts_at": off_grid}, headers=auth())
    assert r.status_code == 422

    past = dt.datetime.combine(in_days(-1), dt.time(10, 0), tzinfo=TZ).isoformat()
    r = await client.post("/api/masters/anna_nails/appointments",
                          json={"service_id": 1, "starts_at": past}, headers=auth())
    assert r.status_code == 422


async def test_naive_datetime_and_bad_phone_rejected(ctx):
    client, _ = ctx
    r = await client.post("/api/masters/anna_nails/appointments",
                          json={"service_id": 1, "starts_at": "2030-01-01T10:00:00"},
                          headers=auth())
    assert r.status_code == 422
    slot = await first_slot(client, in_days(3))
    r = await client.post("/api/masters/anna_nails/appointments",
                          json={"service_id": 1, "starts_at": slot, "phone": "123"},
                          headers=auth())
    assert r.status_code == 422


@pytest.mark.parametrize("headers", [
    {},
    {"X-Channel": "max"},
    {"Authorization": "tma garbage", "X-Channel": "max"},
    "wrong_token",
    "wrong_channel",
])
async def test_booking_requires_valid_auth(ctx, headers):
    client, _ = ctx
    if headers == "wrong_token":
        headers = auth(token="someone-else")
    elif headers == "wrong_channel":
        headers = auth(channel="telegram")  # подписано токеном MAX
    slot = await first_slot(client, in_days(3))
    r = await client.post("/api/masters/anna_nails/appointments",
                          json={"service_id": 1, "starts_at": slot}, headers=headers)
    assert r.status_code == 401
