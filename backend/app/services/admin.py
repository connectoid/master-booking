"""Админка: сводка по сервису и мастерам. Данные клиентов (имена, телефоны) сюда не попадают."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ACTIVE_STATUSES,
    Appointment,
    AppointmentStatus,
    Client,
    Master,
    MasterAccount,
    Service,
    WorkingInterval,
)

ACTIVE = [s.value for s in ACTIVE_STATUSES]


@dataclass
class MasterRow:
    master: Master
    channels: list[str]
    services: int
    has_schedule: bool
    clients: int
    appointments_total: int
    appointments_30d: int        # созданы за 30 дней (кроме отменённых)
    upcoming: int
    last_booking_at: dt.datetime | None  # когда создана последняя запись

    @property
    def status(self) -> str:
        """blocked | setup (не настроен) | idle (нет записей 30 дней) | active."""
        if self.master.blocked_at is not None:
            return "blocked"
        if not self.services or not self.has_schedule:
            return "setup"
        if not self.appointments_30d and not self.upcoming:
            return "idle"
        return "active"


def _per_master(stmt):
    return {row[0]: row[1] for row in stmt}


async def master_rows(session: AsyncSession, now: dt.datetime, master_id: int | None = None) -> list[MasterRow]:
    month_ago = now - dt.timedelta(days=30)
    where = [Master.id == master_id] if master_id else []
    masters = list(await session.scalars(select(Master).where(*where).order_by(Master.created_at.desc())))
    if not masters:
        return []
    ids = [m.id for m in masters]

    channels: dict[int, list[str]] = {}
    for mid, ch in await session.execute(
        select(MasterAccount.master_id, MasterAccount.channel).where(MasterAccount.master_id.in_(ids))
    ):
        channels.setdefault(mid, []).append(ch)

    def count_by(model, *conds):
        return select(model.master_id, func.count()).where(model.master_id.in_(ids), *conds).group_by(model.master_id)

    services = _per_master(await session.execute(count_by(Service, Service.is_active.is_(True))))
    schedule = _per_master(await session.execute(count_by(WorkingInterval)))
    clients = _per_master(await session.execute(count_by(Client)))
    total = _per_master(await session.execute(count_by(Appointment)))
    recent = _per_master(await session.execute(count_by(
        Appointment, Appointment.created_at >= month_ago,
        Appointment.status != AppointmentStatus.CANCELLED.value)))
    upcoming = _per_master(await session.execute(count_by(
        Appointment, Appointment.starts_at >= now, Appointment.status.in_(ACTIVE))))
    last = _per_master(await session.execute(
        select(Appointment.master_id, func.max(Appointment.created_at))
        .where(Appointment.master_id.in_(ids)).group_by(Appointment.master_id)))

    return [
        MasterRow(
            master=m, channels=sorted(channels.get(m.id, [])), services=services.get(m.id, 0),
            has_schedule=bool(schedule.get(m.id)), clients=clients.get(m.id, 0),
            appointments_total=total.get(m.id, 0), appointments_30d=recent.get(m.id, 0),
            upcoming=upcoming.get(m.id, 0), last_booking_at=last.get(m.id),
        )
        for m in masters
    ]


async def stats(session: AsyncSession, now: dt.datetime, days: int = 30) -> dict:
    since = now - dt.timedelta(days=days)
    week = now - dt.timedelta(days=7)

    async def count(model, *conds) -> int:
        return await session.scalar(select(func.count()).select_from(model).where(*conds)) or 0

    by_source = {
        src: n for src, n in await session.execute(
            select(Appointment.source, func.count())
            .where(Appointment.created_at >= since, Appointment.status != AppointmentStatus.CANCELLED.value)
            .group_by(Appointment.source))
    }
    # записи по дням (создание), для графика активности
    day = func.date_trunc("day", func.timezone("UTC", Appointment.created_at))  # сутки по UTC
    daily = {
        d.date(): n for d, n in await session.execute(
            select(day, func.count()).where(Appointment.created_at >= since).group_by(day))
    }
    start = since.date()
    return {
        "masters_total": await count(Master),
        "masters_new_7d": await count(Master, Master.created_at >= week),
        "masters_new_30d": await count(Master, Master.created_at >= since),
        "masters_active_30d": await session.scalar(
            select(func.count(func.distinct(Appointment.master_id))).where(Appointment.created_at >= since)) or 0,
        "masters_blocked": await count(Master, Master.blocked_at.is_not(None)),
        "clients_total": await count(Client),
        "appointments_total": await count(Appointment),
        "appointments_7d": await count(Appointment, Appointment.created_at >= week,
                                       Appointment.status != AppointmentStatus.CANCELLED.value),
        "appointments_30d": sum(by_source.values()),
        "cancelled_30d": await count(Appointment, Appointment.created_at >= since,
                                     Appointment.status == AppointmentStatus.CANCELLED.value),
        "upcoming": await count(Appointment, Appointment.starts_at >= now, Appointment.status.in_(ACTIVE)),
        "by_source_30d": by_source,
        "daily": [{"date": (start + dt.timedelta(days=i)).isoformat(),
                   "count": daily.get(start + dt.timedelta(days=i), 0)} for i in range(days + 1)],
    }


async def master_services(session: AsyncSession, master_id: int) -> list[Service]:
    return list(await session.scalars(
        select(Service).where(Service.master_id == master_id).order_by(Service.position, Service.id)))
