"""Сценарии мастера: регистрация, услуги, график, просмотр записей. Без привязки к мессенджеру."""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.parsing import DateOverrides, ParsedService, Weekly
from app.domain.slugs import slug_base
from app.models import (
    ACTIVE_STATUSES,
    Appointment,
    Channel,
    Client,
    Master,
    MasterAccount,
    ScheduleOverride,
    Service,
    WorkingInterval,
)

TRIAL_DAYS = 30

async def generate_slug(session: AsyncSession, name: str) -> str:
    base = slug_base(name)
    for _ in range(20):
        slug = f"{base}_{random.randint(100, 999)}"
        exists = await session.scalar(select(func.count()).where(Master.slug == slug))
        if not exists:
            return slug
    return f"{base}_{random.randint(10000, 99999)}"


async def get_master_by_account(
    session: AsyncSession, channel: Channel, external_user_id: str
) -> Master | None:
    return await session.scalar(
        select(Master)
        .join(MasterAccount, MasterAccount.master_id == Master.id)
        .where(
            MasterAccount.channel == channel.value,
            MasterAccount.external_user_id == external_user_id,
        )
        .order_by(Master.id)
        .limit(1)
    )


async def get_master_by_slug(session: AsyncSession, slug: str) -> Master | None:
    return await session.scalar(select(Master).where(Master.slug == slug))


async def create_master(
    session: AsyncSession,
    channel: Channel,
    external_user_id: str,
    name: str,
    timezone: str,
    now: dt.datetime | None = None,
) -> Master:
    now = now or dt.datetime.now(dt.UTC)
    master = Master(
        slug=await generate_slug(session, name),
        name=name[:120],
        timezone=timezone,
        trial_ends_at=now + dt.timedelta(days=TRIAL_DAYS),
    )
    session.add(master)
    await session.flush()
    session.add(MasterAccount(
        master_id=master.id, channel=channel.value, external_user_id=external_user_id
    ))
    await session.flush()
    return master


async def master_accounts(session: AsyncSession, master_id: int) -> list[MasterAccount]:
    return list(await session.scalars(
        select(MasterAccount).where(MasterAccount.master_id == master_id)
    ))


async def active_services(session: AsyncSession, master_id: int) -> list[Service]:
    return list(await session.scalars(
        select(Service)
        .where(Service.master_id == master_id, Service.is_active.is_(True))
        .order_by(Service.position, Service.id)
    ))


async def add_services(
    session: AsyncSession, master: Master, items: list[ParsedService]
) -> list[Service]:
    last = await session.scalar(
        select(func.coalesce(func.max(Service.position), 0)).where(Service.master_id == master.id)
    )
    created = []
    for i, item in enumerate(items, start=1):
        service = Service(
            master_id=master.id, name=item.name, duration_min=item.duration_min,
            price=item.price, position=last + i,
        )
        session.add(service)
        created.append(service)
    await session.flush()
    return created


async def deactivate_service(session: AsyncSession, master: Master, service_id: int) -> bool:
    service = await session.scalar(
        select(Service).where(Service.id == service_id, Service.master_id == master.id)
    )
    if service is None or not service.is_active:
        return False
    service.is_active = False  # не удаляем: на услугу могут ссылаться записи
    await session.flush()
    return True


async def get_weekly_schedule(session: AsyncSession, master_id: int) -> Weekly:
    weekly: Weekly = {}
    for wi in await session.scalars(
        select(WorkingInterval)
        .where(WorkingInterval.master_id == master_id)
        .order_by(WorkingInterval.weekday, WorkingInterval.start_time)
    ):
        weekly.setdefault(wi.weekday, []).append((wi.start_time, wi.end_time))
    return weekly


async def set_weekly_schedule(session: AsyncSession, master: Master, weekly: Weekly) -> None:
    await session.execute(delete(WorkingInterval).where(WorkingInterval.master_id == master.id))
    for weekday, ranges in weekly.items():
        for start, end in ranges:
            session.add(WorkingInterval(
                master_id=master.id, weekday=weekday, start_time=start, end_time=end
            ))
    await session.flush()


async def appointments_between(
    session: AsyncSession, master_id: int, start: dt.datetime, end: dt.datetime
) -> list[tuple[Appointment, Client]]:
    rows = await session.execute(
        select(Appointment, Client)
        .join(Client, Client.id == Appointment.client_id)
        .where(
            Appointment.master_id == master_id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at < end,
            Appointment.ends_at > start,
        )
        .order_by(Appointment.starts_at)
    )
    return [(a, c) for a, c in rows]


# ---------- исключения по датам: выходные, отпуск, другие часы ----------

async def upcoming_overrides(
    session: AsyncSession, master_id: int, from_day: dt.date
) -> DateOverrides:
    result: DateOverrides = {}
    for ov in await session.scalars(
        select(ScheduleOverride)
        .where(ScheduleOverride.master_id == master_id, ScheduleOverride.day >= from_day)
        .order_by(ScheduleOverride.day, ScheduleOverride.start_time)
    ):
        ranges = result.setdefault(ov.day, [])
        if ov.start_time is not None and ov.end_time is not None:
            ranges.append((ov.start_time, ov.end_time))
    return result


async def set_overrides(session: AsyncSession, master: Master, overrides: DateOverrides) -> None:
    """Заменяет исключения на указанные даты. Пустой список часов — выходной."""
    if not overrides:
        return
    await session.execute(delete(ScheduleOverride).where(
        ScheduleOverride.master_id == master.id,
        ScheduleOverride.day.in_(list(overrides)),
    ))
    for day, ranges in overrides.items():
        if not ranges:
            session.add(ScheduleOverride(master_id=master.id, day=day))
        for start, end in ranges:
            session.add(ScheduleOverride(
                master_id=master.id, day=day, start_time=start, end_time=end
            ))
    await session.flush()


async def delete_overrides(
    session: AsyncSession, master: Master, start: dt.date, end: dt.date
) -> int:
    result = await session.execute(delete(ScheduleOverride).where(
        ScheduleOverride.master_id == master.id,
        ScheduleOverride.day.between(start, end),
    ))
    return result.rowcount or 0


async def appointments_on_days(
    session: AsyncSession, master: Master, days: list[dt.date]
) -> list[tuple[Appointment, Client]]:
    """Активные записи на указанные даты (по времени мастера)."""
    if not days:
        return []
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(master.timezone)
    wanted = set(days)
    start = dt.datetime.combine(min(days), dt.time.min, tzinfo=tz)
    end = dt.datetime.combine(max(days) + dt.timedelta(days=1), dt.time.min, tzinfo=tz)
    rows = await appointments_between(session, master.id, start, end)
    return [(a, c) for a, c in rows if a.starts_at.astimezone(tz).date() in wanted]


# ---------- настройки ----------

SETTINGS_FIELDS = {"min_lead_min", "horizon_days", "one_booking_per_day", "timezone"}


async def update_settings(session: AsyncSession, master: Master, **fields) -> None:
    for key, value in fields.items():
        if key not in SETTINGS_FIELDS:
            raise ValueError(key)
        setattr(master, key, value)
    await session.flush()
