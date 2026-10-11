"""Сценарии записи: слоты, создание и отмена записи.

Не зависит от HTTP — те же функции будет вызывать бот мастера (ручная запись).
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
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

MAX_DAYS_PER_REQUEST = 31


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


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# ---------- чтение ----------

async def get_master(session: AsyncSession, slug: str) -> Master:
    master = await session.scalar(select(Master).where(Master.slug == slug))
    if master is None:
        raise NotFound("Мастер не найден")
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
) -> dict[dt.date, list[dt.datetime]]:
    """Свободные слоты на услугу в диапазоне дат (локальные даты мастера)."""
    tz = ZoneInfo(master.timezone)
    today = master_today(master, now)
    date_from = max(date_from, today)
    date_to = min(
        date_to,
        today + dt.timedelta(days=master.horizon_days),
        date_from + dt.timedelta(days=MAX_DAYS_PER_REQUEST - 1),
    )
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
            min_lead=dt.timedelta(minutes=master.min_lead_min),
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
