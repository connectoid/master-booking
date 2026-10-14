"""Админка владельца сервиса: /api/admin/... (заголовок «Authorization: admin <токен>»).

Вход — по ссылке из бота MAX (команда /admin), только для ID из ADMIN_MAX_IDS.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.ui import Button
from app.channels import links, web_auth
from app.config import Settings, get_settings
from app.db import get_session
from app.models import Channel, Master
from app.runtime import runtime
from app.services import admin as svc
from app.services import booking
from app.services import masters as ms

router = APIRouter(prefix="/api/admin", tags=["admin"])
Session = Annotated[AsyncSession, Depends(get_session)]
Conf = Annotated[Settings, Depends(get_settings)]


async def current_admin(settings: Conf, authorization: Annotated[str | None, Header()] = None) -> str:
    scheme, _, value = (authorization or "").partition(" ")
    admin_id = web_auth.verify_admin_token(settings, value.strip()) if scheme.lower() == "admin" else None
    if admin_id is None:
        raise HTTPException(401, "Войдите заново: команда /admin в боте MAX")
    return admin_id


Admin = Annotated[str, Depends(current_admin)]


class MasterBrief(BaseModel):
    id: int
    slug: str
    name: str
    created_at: dt.datetime
    channels: list[str]
    status: str
    services: int
    has_schedule: bool
    clients: int
    appointments_total: int
    appointments_30d: int
    upcoming: int
    last_booking_at: dt.datetime | None
    paid_until: dt.datetime | None
    blocked_at: dt.datetime | None
    admin_note: str | None
    site_url: str | None


class ServiceInfo(BaseModel):
    name: str
    duration_min: int
    price: int | None
    is_active: bool


class MasterDetail(MasterBrief):
    timezone: str
    schedule: str
    services_list: list[ServiceInfo]


class MasterPatch(BaseModel):
    paid_until: dt.date | None = None   # по Москве, до конца дня; null — снять
    blocked: bool | None = None
    admin_note: str | None = Field(default=None, max_length=2000)


class Extend(BaseModel):
    days: int = Field(ge=1, le=3660)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class Sent(BaseModel):
    sent: int


def _brief(row: svc.MasterRow) -> dict:
    m = row.master
    return dict(
        id=m.id, slug=m.slug, name=m.name, created_at=m.created_at, channels=row.channels,
        status=row.status, services=row.services, has_schedule=row.has_schedule, clients=row.clients,
        appointments_total=row.appointments_total, appointments_30d=row.appointments_30d,
        upcoming=row.upcoming, last_booking_at=row.last_booking_at, paid_until=m.paid_until,
        blocked_at=m.blocked_at, admin_note=m.admin_note, site_url=links.site_link(m.slug),
    )


async def _row(session: AsyncSession, master_id: int) -> svc.MasterRow:
    rows = await svc.master_rows(session, booking.utcnow(), master_id)
    if not rows:
        raise HTTPException(404, "Мастер не найден")
    return rows[0]


async def _detail(session: AsyncSession, master_id: int) -> MasterDetail:
    from app.bot.parsing import format_schedule

    row = await _row(session, master_id)
    weekly = await ms.get_weekly_schedule(session, master_id)
    services = await svc.master_services(session, master_id)
    return MasterDetail(
        **_brief(row), timezone=row.master.timezone,
        schedule=format_schedule(weekly) if weekly else "не задан",
        services_list=[ServiceInfo(name=s.name, duration_min=s.duration_min, price=s.price,
                                   is_active=s.is_active) for s in services],
    )


@router.get("/stats")
async def stats(_: Admin, session: Session) -> dict:
    return await svc.stats(session, booking.utcnow())


@router.get("/masters", response_model=list[MasterBrief])
async def masters(_: Admin, session: Session,
                  q: Annotated[str, Query(max_length=60)] = "",
                  status: Annotated[str, Query(pattern="^(all|active|idle|setup|blocked)$")] = "all"):
    rows = await svc.master_rows(session, booking.utcnow())
    needle = q.strip().casefold()
    out = []
    for r in rows:
        if status != "all" and r.status != status:
            continue
        if needle and needle not in f"{r.master.name} {r.master.slug}".casefold():
            continue
        out.append(MasterBrief(**_brief(r)))
    return out


@router.get("/masters/{master_id}", response_model=MasterDetail)
async def master_detail(master_id: int, _: Admin, session: Session):
    return await _detail(session, master_id)


@router.patch("/masters/{master_id}", response_model=MasterDetail)
async def patch_master(master_id: int, body: MasterPatch, _: Admin, session: Session):
    master = await session.get(Master, master_id)
    if master is None:
        raise HTTPException(404, "Мастер не найден")
    fields = body.model_dump(exclude_unset=True)
    if "paid_until" in fields:
        d = fields["paid_until"]
        master.paid_until = (dt.datetime.combine(d, dt.time(23, 59, 59), tzinfo=ZoneInfo("Europe/Moscow"))
                             if d else None)
    if fields.get("blocked") is not None:
        if fields["blocked"] and master.blocked_at is None:
            master.blocked_at = booking.utcnow()
        elif not fields["blocked"]:
            master.blocked_at = None
    if "admin_note" in fields:
        master.admin_note = (fields["admin_note"] or "").strip() or None
    await session.commit()
    return await _detail(session, master_id)


@router.post("/masters/{master_id}/extend", response_model=MasterDetail)
async def extend(master_id: int, body: Extend, _: Admin, session: Session):
    """Продлить доступ на N дней — от текущей даты окончания или от сегодня, если она прошла."""
    master = await session.get(Master, master_id)
    if master is None:
        raise HTTPException(404, "Мастер не найден")
    now = booking.utcnow()
    msk = ZoneInfo("Europe/Moscow")
    base = master.paid_until if master.paid_until and master.paid_until > now else now
    end_day = base.astimezone(msk).date() + dt.timedelta(days=body.days)
    master.paid_until = dt.datetime.combine(end_day, dt.time(23, 59, 59), tzinfo=msk)
    await session.commit()
    return await _detail(session, master_id)


@router.post("/masters/{master_id}/message", response_model=Sent)
async def message(master_id: int, body: Message, _: Admin, session: Session):
    """Написать мастеру от имени сервиса — в бот, во все его мессенджеры."""
    master = await session.get(Master, master_id)
    if master is None:
        raise HTTPException(404, "Мастер не найден")
    sent = 0
    for acc in await ms.master_accounts(session, master.id):
        messenger = runtime.messengers.get(Channel(acc.channel))
        if messenger is None:
            continue
        try:
            await messenger.send(acc.external_user_id, f"Сообщение от команды «Окошко»:\n\n{body.text.strip()}",
                                 [[Button("Меню", payload="m:menu")]])
            sent += 1
        except Exception:
            pass
    if not sent:
        raise HTTPException(502, "Не удалось доставить сообщение: мессенджер мастера недоступен")
    return Sent(sent=sent)
