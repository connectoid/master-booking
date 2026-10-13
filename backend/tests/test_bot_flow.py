"""Сквозной сценарий бота на реальном PostgreSQL (нужен TEST_DATABASE_URL).

Мастер регистрируется, заводит услуги и график; клиент записывается; приходят уведомления
и напоминания; клиент отменяет запись кнопкой.
"""
import datetime as dt
import re
from zoneinfo import ZoneInfo

import pytest

from app.bot.ui import Event
from app.models import Channel

MASTER = "900"
CLIENT = "111"
BOT = "id123_bot"


class FakeMessenger:
    def __init__(self):
        self.sent: list[tuple[str, str, list | None]] = []
        self.toasts: list[str] = []

    async def send(self, user_id, text, keyboard=None):
        self.sent.append((user_id, text, keyboard))

    async def answer_callback(self, callback_id, notification):
        self.toasts.append(notification)

    def last(self, user_id=MASTER) -> tuple[str, list | None]:
        for uid, text, kb in reversed(self.sent):
            if uid == user_id:
                return text, kb
        raise AssertionError(f"нет сообщений для {user_id}")

    def payloads(self, user_id=MASTER) -> list[str]:
        _, kb = self.last(user_id)
        return [b.payload for row in (kb or []) for b in row if b.payload]


@pytest.fixture
async def bot(migrated_db):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.runtime import runtime

    engine = create_async_engine(migrated_db.replace("+psycopg", "+asyncpg"), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE masters, bot_states RESTART IDENTITY CASCADE"))
    fake = FakeMessenger()
    runtime.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    runtime.messengers[Channel.MAX] = fake
    runtime.bot_usernames[Channel.MAX] = BOT
    try:
        yield fake, engine
    finally:
        runtime.reset()
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE masters, bot_states RESTART IDENTITY CASCADE"))
        await engine.dispose()


async def send(kind, user=MASTER, text="", payload="", name="Анна"):
    from app.bot.handlers import process_event

    await process_event(Channel.MAX, Event(
        kind=kind, user_id=user, name=name, text=text, payload=payload,
        callback_id="cb" if kind == "callback" else None,
    ))


async def scalar(engine, sql):
    from sqlalchemy import text

    async with engine.begin() as conn:  # begin — чтобы UPDATE в тестах фиксировались
        return (await conn.execute(text(sql))).scalar()


async def register_master(fake):
    await send("start")
    assert "m:register" in fake.payloads()
    await send("callback", payload="m:register")
    await send("text", text="Анна, маникюр")
    assert "m:tz:Asia/Kamchatka" in fake.payloads()
    await send("callback", payload="m:tz:Asia/Kamchatka")


async def test_onboarding_services_schedule_link(bot):
    fake, engine = bot
    await register_master(fake)
    assert await scalar(engine, "SELECT name || '|' || timezone FROM masters") == \
        "Анна, маникюр|Asia/Kamchatka"
    slug = await scalar(engine, "SELECT slug FROM masters")
    assert re.fullmatch(r"anna_manikyur_\d{3}", slug)
    assert "услуг" in fake.last()[0].lower()

    # ошибка ввода не сбрасывает шаг
    await send("text", text="просто текст")
    assert "Пример" in fake.last()[0]
    await send("text", text="Маникюр, 90, 1500\nСнятие; 30мин; 300")
    assert await scalar(engine, "SELECT count(*) FROM services") == 2
    assert "график" in fake.last()[0].lower()  # после первых услуг ведём к графику

    await send("text", text="пн-вс 10-18")
    assert await scalar(engine, "SELECT count(*) FROM working_intervals") == 7
    text, _ = fake.last()
    assert f"https://max.ru/{BOT}?startapp={slug}" in text

    # меню и списки
    await send("text", text="привет")
    assert "m:today" in fake.payloads()
    await send("callback", payload="m:services")
    assert "Маникюр — 90 мин, 1 500 ₽" in fake.last()[0]
    svc_id = await scalar(engine, "SELECT id FROM services WHERE name = 'Снятие'")
    await send("callback", payload=f"m:svc_del:{svc_id}")
    assert "Снятие" not in fake.last()[0]
    await send("callback", payload="m:schedule")
    assert "Пн–Вс: 10:00–18:00" in fake.last()[0]


async def test_booking_notifications_reminders_and_cancel(bot):
    from app import notify
    from app.channels.identity import ChannelUser
    from app.runtime import runtime
    from app.services import booking
    from app.workers.reminders import process_due

    fake, engine = bot
    await register_master(fake)
    await send("text", text="Маникюр, 90, 1500")
    await send("text", text="пн-вс 10-18")
    slug = await scalar(engine, "SELECT slug FROM masters")

    # клиент пришёл по ссылке на бота
    await send("start", user=CLIENT, payload=slug, name="Ольга")
    text, kb = fake.last(CLIENT)
    assert "Анна, маникюр" in text and kb[0][0].url.endswith(f"startapp={slug}")
    await send("start", user=CLIENT, payload="nobody_000", name="Ольга")
    assert "не найден" in fake.last(CLIENT)[0]

    # запись (как из Mini App) — через сервис
    tz = ZoneInfo("Asia/Kamchatka")
    day = dt.datetime.now(tz).date() + dt.timedelta(days=3)
    async with runtime.session_factory() as s:
        master = await booking.get_master(s, slug)
        service = (await booking.list_services(s, master.id))[0]
        slots = await booking.compute_slots(s, master, service, day, day, booking.utcnow())
        appt = await booking.create_appointment(
            s, master, service, slots[day][0], ChannelUser(Channel.MAX, CLIENT, "Ольга"),
            phone="+79140000000",
        )
    await notify.appointment_created(appt.id)
    assert "Новая запись" in fake.last(MASTER)[0] and "Ольга, +79140000000" in fake.last(MASTER)[0]
    assert "Вы записаны" in fake.last(CLIENT)[0]

    # запись видна мастеру
    await send("callback", payload="m:week")
    assert "Маникюр — Ольга" in fake.last()[0]

    # напоминание «за сутки» созрело
    await scalar(engine, "UPDATE reminders SET send_at = now() - interval '1 minute' "
                         "WHERE kind = 'day_before' RETURNING id")
    assert await process_due() == 1
    text, kb = fake.last(CLIENT)
    assert text.startswith("Завтра") and kb[0][1].payload == f"c:cancel:{appt.id}"
    assert await scalar(engine, "SELECT status FROM reminders WHERE kind='day_before'") == "sent"
    assert await process_due() == 0  # повторно не шлём

    # чужой пользователь не может отменить
    await send("callback", user="222", payload=f"c:cancel:{appt.id}")
    assert await scalar(engine, f"SELECT status FROM appointments WHERE id={appt.id}") == \
        "confirmed"

    # клиент отменяет кнопкой из напоминания
    await send("callback", user=CLIENT, payload=f"c:cancel:{appt.id}")
    assert await scalar(engine, f"SELECT status FROM appointments WHERE id={appt.id}") == \
        "cancelled"
    assert "Клиент отменил запись" in fake.last(MASTER)[0]
    assert await scalar(engine, "SELECT count(*) FROM reminders WHERE status='pending'") == 0


async def test_webhook_secret_and_dispatch(bot):
    from httpx import ASGITransport, AsyncClient

    from app.config import Settings, get_settings
    from app.main import app

    fake, _ = bot
    app.dependency_overrides[get_settings] = lambda: Settings(max_webhook_secret="s3cret_value")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            update = {"update_type": "bot_started", "timestamp": 1,
                      "user": {"user_id": 555, "first_name": "Ира"}, "payload": ""}
            r = await c.post("/max/webhook", json=update)
            assert r.status_code == 403
            r = await c.post("/max/webhook", json=update,
                             headers={"X-Max-Bot-Api-Secret": "wrong"})
            assert r.status_code == 403
            r = await c.post("/max/webhook", json=update,
                             headers={"X-Max-Bot-Api-Secret": "s3cret_value"})
            assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert "Окошко" in fake.last("555")[0]


async def test_days_off_week_schedule_and_settings(bot):
    from app.runtime import runtime
    from app.services import booking

    fake, engine = bot
    await register_master(fake)
    await send("text", text="Маникюр, 60, 1000")
    await send("text", text="пн-вс 10-18")
    slug = await scalar(engine, "SELECT slug FROM masters")
    tz = ZoneInfo("Asia/Kamchatka")
    today = dt.datetime.now(tz).date()
    d1, d2 = today + dt.timedelta(days=5), today + dt.timedelta(days=6)

    async def slots_on(day):
        async with runtime.session_factory() as s:
            m = await booking.get_master(s, slug)
            svc = (await booking.list_services(s, m.id))[0]
            res = await booking.compute_slots(s, m, svc, day, day, booking.utcnow())
            return [x.astimezone(tz).strftime("%H:%M") for x in res.get(day, [])]

    assert (await slots_on(d1))[0] == "10:00"

    # выходной и другие часы
    await send("callback", payload="m:off")
    assert "Особых дней нет" in fake.last()[0]
    await send("callback", payload="m:off_add")
    await send("text", text=f"{d1:%d.%m}\n{d2:%d.%m} 14-16")
    text, kb = fake.last()
    assert "Сохранено" in text and "выходной" in text
    assert await slots_on(d1) == []
    assert await slots_on(d2) == ["14:00", "14:15", "14:30", "14:45", "15:00"]

    # удаление возвращает обычный график
    payload = next(b.payload for row in kb for b in row
                   if b.payload and b.payload.startswith(f"m:off_del:{d1.isoformat()}"))
    await send("callback", payload=payload)
    assert (await slots_on(d1))[0] == "10:00"

    # график на неделю
    start = today + dt.timedelta(days=10)
    end = start + dt.timedelta(days=6)
    await send("callback", payload="m:week_add")
    await send("text", text=f"{start:%d.%m}-{end:%d.%m}")
    assert "Период" in fake.last()[0]
    await send("text", text="пн-пт 12-14")
    assert "Особый график" in fake.last()[0]
    weekend = next(start + dt.timedelta(days=i) for i in range(7)
                   if (start + dt.timedelta(days=i)).weekday() == 6)
    weekday = next(start + dt.timedelta(days=i) for i in range(7)
                   if (start + dt.timedelta(days=i)).weekday() == 0)
    assert await slots_on(weekend) == []
    assert (await slots_on(weekday))[0] == "12:00"

    # настройки: минимум за сутки, одна запись в день, горизонт
    await send("callback", payload="m:settings")
    assert "Одна запись в день на клиента: выключено" in fake.last()[0]
    await send("callback", payload="m:lead:1440")
    await send("callback", payload="m:oneday")
    await send("callback", payload="m:horizon:14")
    text = fake.last()[0]
    assert "не позже чем за сутки" in text and "включено" in text and "14 дн." in text
    assert await scalar(engine, "SELECT min_lead_min FROM masters") == 1440
    assert await slots_on(today) == []  # сегодня — меньше суток
    assert await slots_on(today + dt.timedelta(days=20)) == []  # за горизонтом


async def test_one_booking_per_day_limit(bot):
    from app.channels.identity import ChannelUser
    from app.runtime import runtime
    from app.services import booking

    fake, engine = bot
    await register_master(fake)
    await send("text", text="Маникюр, 60, 1000")
    await send("text", text="пн-вс 10-18")
    slug = await scalar(engine, "SELECT slug FROM masters")
    tz = ZoneInfo("Asia/Kamchatka")
    day = dt.datetime.now(tz).date() + dt.timedelta(days=3)
    client = ChannelUser(Channel.MAX, CLIENT, "Ольга")

    async def book(offset):
        async with runtime.session_factory() as s:
            m = await booking.get_master(s, slug)
            svc = (await booking.list_services(s, m.id))[0]
            slots = await booking.compute_slots(s, m, svc, day + dt.timedelta(days=offset),
                                                day + dt.timedelta(days=offset), booking.utcnow())
            return await booking.create_appointment(
                s, m, svc, slots[day + dt.timedelta(days=offset)][0], client)

    await book(0)
    await book(0)  # ограничение выключено — вторая запись в тот же день можно
    await scalar(engine, "UPDATE masters SET one_booking_per_day = true RETURNING id")
    with pytest.raises(booking.DailyLimit):
        await book(0)
    await book(1)  # другой день — можно
    assert await scalar(engine, "SELECT count(*) FROM appointments") == 3
