"""Сценарии записи: слоты, создание и отмена записи.

Не зависит от HTTP — те же функции будет вызывать бот мастера (ручная запись).
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.identity import ChannelUser
from app.domain.reminders import plan_reminders
from app.domain.slots import SlotRules, available_slots
from app.models import (
    ACTIVE_STATUSES,
    Appointment,
    AppointmentStatus,
    Client,
    ClientContact,
    Master,
    Reminder,
    ReminderStatus,
    ScheduleOverride,
    Service,
    WorkingInterval,
)

MAX_DAYS_PER_REQUEST = 92  # максимум горизонта записи (90 дней) + запас


class BookingError(Exception):
    """Ошибка сценария записи; message показывается пользователю."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(BookingError):
    pass


class SlotUnavailable(BookingError):
    pass


class SlotTaken(BookingError):
    pass


class Forbidden(BookingError):
    pass


class DailyLimit(BookingError):
    pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# ---------- чтение ----------

async def get_master(session: AsyncSession, slug: str) -> Master:
    master = await session.scalar(select(Master).where(Master.slug == slug))
    if master is None:
        raise NotFound("Мастер не найден")
    if master.blocked_at is not None:
        raise NotFound("Онлайн-запись к этому мастеру сейчас недоступна")
    return master


async def list_services(session: AsyncSession, master_id: int) -> list[Service]:
    rows = await session.scalars(
        select(Service)
        .where(Service.master_id == master_id, Service.is_active.is_(True))
        .order_by(Service.position, Service.id)
    )
    return list(rows)


async def get_service(session: AsyncSession, master: Master, service_id: int) -> Service:
    service = await session.scalar(
        select(Service).where(
            Service.id == service_id,
            Service.master_id == master.id,
            Service.is_active.is_(True),
        )
    )
    if service is None:
        raise NotFound("Услуга не найдена")
    return service


def master_today(master: Master, now: dt.datetime) -> dt.date:
    return now.astimezone(ZoneInfo(master.timezone)).date()


async def compute_slots(
    session: AsyncSession,
    master: Master,
    service: Service,
    date_from: dt.date,
    date_to: dt.date,
    now: dt.datetime,
    *,
    for_master: bool = False,
) -> dict[dt.date, list[dt.datetime]]:
    """Свободные слоты на услугу в диапазоне дат (локальные даты мастера).

    for_master=True — подсказки для ручной записи: без «минимума до визита» и горизонта.
    """
    tz = ZoneInfo(master.timezone)
    today = master_today(master, now)
    date_from = max(date_from, today)
    limits = [date_to, date_from + dt.timedelta(days=MAX_DAYS_PER_REQUEST - 1)]
    if not for_master:
        limits.append(today + dt.timedelta(days=master.horizon_days))
    date_to = min(limits)
    if date_to < date_from:
        return {}

    weekly: dict[int, list[tuple[dt.time, dt.time]]] = {}
    for wi in await session.scalars(
        select(WorkingInterval).where(WorkingInterval.master_id == master.id)
    ):
        weekly.setdefault(wi.weekday, []).append((wi.start_time, wi.end_time))

    overrides: dict[dt.date, list[tuple[dt.time, dt.time]]] = {}
    for ov in await session.scalars(
        select(ScheduleOverride).where(
            ScheduleOverride.master_id == master.id,
            ScheduleOverride.day.between(date_from, date_to),
        )
    ):
        day_ranges = overrides.setdefault(ov.day, [])  # пустой список = выходной
        if ov.start_time is not None and ov.end_time is not None:
            day_ranges.append((ov.start_time, ov.end_time))

    buffer = dt.timedelta(minutes=master.buffer_min)
    range_start = dt.datetime.combine(date_from, dt.time.min, tzinfo=tz) - buffer
    range_end = dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time.min, tzinfo=tz) + buffer
    busy = [
        (a.starts_at, a.ends_at)
        for a in await session.execute(
            select(Appointment.starts_at, Appointment.ends_at).where(
                Appointment.master_id == master.id,
                Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
                Appointment.starts_at < range_end,
                Appointment.ends_at > range_start,
            )
        )
    ]

    return available_slots(
        day_from=date_from,
        day_to=date_to,
        tz=tz,
        weekly=weekly,
        overrides=overrides,
        busy=busy,
        rules=SlotRules(
            duration=dt.timedelta(minutes=service.duration_min),
            step=dt.timedelta(minutes=master.slot_step_min),
            min_lead=dt.timedelta(minutes=0 if for_master else master.min_lead_min),
            buffer=buffer,
        ),
        now=now,
    )


# ---------- клиенты ----------

async def find_client(session: AsyncSession, master: Master, user: ChannelUser) -> Client | None:
    return await session.scalar(
        select(Client)
        .join(ClientContact, ClientContact.client_id == Client.id)
        .where(
            Client.master_id == master.id,
            ClientContact.channel == user.channel.value,
            ClientContact.external_user_id == user.external_user_id,
        )
    )


async def get_or_create_client(
    session: AsyncSession,
    master: Master,
    user: ChannelUser,
    name: str | None,
    phone: str | None,
) -> Client:
    client = await find_client(session, master, user)
    if client is None:
        client = Client(master_id=master.id, name=(name or user.display_name or "Клиент")[:120])
        session.add(client)
        await session.flush()
        session.add(
            ClientContact(
                client_id=client.id,
                channel=user.channel.value,
                external_user_id=user.external_user_id,
            )
        )
    elif name:
        client.name = name[:120]
    if phone:
        client.phone = phone
    return client


# ---------- запись ----------

async def create_appointment(
    session: AsyncSession,
    master: Master,
    service: Service,
    starts_at: dt.datetime,
    user: ChannelUser,
    *,
    client_name: str | None = None,
    phone: str | None = None,
    comment: str | None = None,
    now: dt.datetime | None = None,
) -> Appointment:
    """Создать подтверждённую запись клиента и запланировать напоминания.

    Свободность слота проверяется тем же расчётом, что показывает слоты клиенту;
    гонку двух одновременных записей ловит EXCLUDE-ограничение в БД.
    """
    now = now or utcnow()
    if starts_at.tzinfo is None:
        raise SlotUnavailable("Время записи должно быть с часовым поясом")

    day = starts_at.astimezone(ZoneInfo(master.timezone)).date()
    slots = await compute_slots(session, master, service, day, day, now)
    if starts_at not in slots.get(day, []):
        raise SlotUnavailable("Это время недоступно, выберите другое")

    client = await get_or_create_client(session, master, user, client_name, phone)
    if master.one_booking_per_day:
        await _check_daily_limit(session, master, client, day)
    appointment = Appointment(
        master_id=master.id,
        client_id=client.id,
        service_id=service.id,
        service_name=service.name,
        price=service.price,
        starts_at=starts_at,
        ends_at=starts_at + dt.timedelta(minutes=service.duration_min),
        status=AppointmentStatus.CONFIRMED.value,
        source=user.channel.value,
        comment=(comment or None),
    )
    session.add(appointment)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        if "ex_appointments_no_overlap" in str(e.orig):
            raise SlotTaken("Это время только что заняли, выберите другое") from e
        raise

    for kind, send_at in plan_reminders(appointment.starts_at, now):
        session.add(Reminder(appointment_id=appointment.id, kind=kind, send_at=send_at))

    await session.commit()
    return appointment


async def _check_daily_limit(
    session: AsyncSession, master: Master, client: Client, day: dt.date
) -> None:
    """Не больше одной активной записи клиента на день (по времени мастера)."""
    if client.id is None:
        return
    # блокируем клиента, чтобы две одновременные записи не проскочили проверку
    await session.execute(select(Client.id).where(Client.id == client.id).with_for_update())
    tz = ZoneInfo(master.timezone)
    start = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
    count = await session.scalar(
        select(func.count()).select_from(Appointment).where(
            Appointment.client_id == client.id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at >= start,
            Appointment.starts_at < start + dt.timedelta(days=1),
        )
    )
    if count:
        raise DailyLimit(
            "У вас уже есть запись на этот день. Выберите другой день "
            "или отмените текущую запись в «Мои записи»."
        )


async def list_client_appointments(
    session: AsyncSession, master: Master, user: ChannelUser, now: dt.datetime | None = None
) -> list[Appointment]:
    """Предстоящие активные записи клиента у этого мастера."""
    client = await find_client(session, master, user)
    if client is None:
        return []
    rows = await session.scalars(
        select(Appointment)
        .where(
            Appointment.client_id == client.id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.ends_at > (now or utcnow()),
        )
        .order_by(Appointment.starts_at)
    )
    return list(rows)


async def cancel_appointment_by_client(
    session: AsyncSession, appointment_id: int, user: ChannelUser
) -> Appointment:
    appointment = await session.scalar(
        select(Appointment)
        .join(ClientContact, ClientContact.client_id == Appointment.client_id)
        .where(
            Appointment.id == appointment_id,
            ClientContact.channel == user.channel.value,
            ClientContact.external_user_id == user.external_user_id,
        )
    )
    if appointment is None:
        raise NotFound("Запись не найдена")
    if appointment.status not in [s.value for s in ACTIVE_STATUSES]:
        raise Forbidden("Эту запись уже нельзя отменить")

    appointment.status = AppointmentStatus.CANCELLED.value
    await session.execute(
        update(Reminder)
        .where(
            Reminder.appointment_id == appointment.id,
            Reminder.status == ReminderStatus.PENDING.value,
        )
        .values(status=ReminderStatus.CANCELLED.value)
    )
    await session.commit()
    return appointment



# ---------- записи глазами мастера ----------

async def master_appointments(
    session: AsyncSession, master: Master, start: dt.datetime, end: dt.datetime
) -> list[tuple[Appointment, Client]]:
    rows = await session.execute(
        select(Appointment, Client)
        .join(Client, Client.id == Appointment.client_id)
        .where(
            Appointment.master_id == master.id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at < end,
            Appointment.ends_at > start,
        )
        .order_by(Appointment.starts_at)
    )
    return [(a, c) for a, c in rows]


async def find_clients(session: AsyncSession, master: Master, query: str, limit: int = 10):
    """Поиск клиентов мастера по имени или телефону — для подсказок при ручной записи.

    Фильтруем в Python: регистр кириллицы в БД зависит от её локали, а клиентов у одного
    мастера — сотни, не миллионы.
    """
    clients = list(await session.scalars(
        select(Client).where(Client.master_id == master.id)
        .order_by(Client.created_at.desc()).limit(2000)
    ))
    q = query.strip().casefold()
    if not q:
        return clients[:limit]
    digits = "".join(ch for ch in q if ch.isdigit())
    found = [
        c for c in clients
        if q in c.name.casefold() or (len(digits) >= 3 and c.phone and digits[-10:] in c.phone)
    ]
    return found[:limit]


async def create_manual_appointment(
    session: AsyncSession,
    master: Master,
    service: Service,
    starts_at: dt.datetime,
    *,
    client_name: str,
    phone: str | None = None,
    client_id: int | None = None,
    comment: str | None = None,
    now: dt.datetime | None = None,
) -> Appointment:
    """Мастер записывает клиента сам (позвонил, пришёл лично).

    Рабочие часы и минимум до визита не проверяются — мастер знает, что делает;
    проверяются только прошлое время и пересечение с другими записями.
    Клиент ищется по id или телефону, иначе создаётся новый.
    """
    now = now or utcnow()
    if starts_at.tzinfo is None:
        raise SlotUnavailable("Время записи должно быть с часовым поясом")
    if starts_at < now - dt.timedelta(minutes=5):
        raise SlotUnavailable("Это время уже прошло")

    client = None
    if client_id is not None:
        client = await session.scalar(
            select(Client).where(Client.id == client_id, Client.master_id == master.id)
        )
        if client is None:
            raise NotFound("Клиент не найден")
    elif phone:
        client = await session.scalar(
            select(Client).where(Client.master_id == master.id, Client.phone == phone)
            .order_by(Client.id).limit(1)
        )
    if client is None:
        client = Client(master_id=master.id, name=client_name[:120], phone=phone)
        session.add(client)
        await session.flush()
    else:
        if client_name:
            client.name = client_name[:120]
        if phone:
            client.phone = phone

    appointment = Appointment(
        master_id=master.id,
        client_id=client.id,
        service_id=service.id,
        service_name=service.name,
        price=service.price,
        starts_at=starts_at,
        ends_at=starts_at + dt.timedelta(minutes=service.duration_min),
        status=AppointmentStatus.CONFIRMED.value,
        source="manual",
        comment=comment or None,
    )
    session.add(appointment)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        if "ex_appointments_no_overlap" in str(e.orig):
            raise SlotTaken("На это время уже есть запись") from e
        raise

    # напоминания — только если у клиента есть мессенджер (например, записывался раньше сам)
    has_contact = await session.scalar(
        select(func.count()).select_from(ClientContact).where(ClientContact.client_id == client.id)
    )
    if has_contact:
        for kind, send_at in plan_reminders(appointment.starts_at, now):
            session.add(Reminder(appointment_id=appointment.id, kind=kind, send_at=send_at))
    await session.commit()
    return appointment


async def cancel_appointment_by_master(
    session: AsyncSession, master: Master, appointment_id: int
) -> Appointment:
    appointment = await session.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id, Appointment.master_id == master.id
        )
    )
    if appointment is None:
        raise NotFound("Запись не найдена")
    if appointment.status not in [s.value for s in ACTIVE_STATUSES]:
        raise Forbidden("Запись уже отменена или завершена")
    appointment.status = AppointmentStatus.CANCELLED.value
    await session.execute(
        update(Reminder)
        .where(
            Reminder.appointment_id == appointment.id,
            Reminder.status == ReminderStatus.PENDING.value,
        )
        .values(status=ReminderStatus.CANCELLED.value)
    )
    await session.commit()
    return appointment
