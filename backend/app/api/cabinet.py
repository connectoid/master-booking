"""API кабинета мастера (Mini App): график, особые дни, настройки.

Доступ — только мастеру: пользователь из подписанного initData должен быть привязан
к мастеру в master_accounts. Параметрам из адреса не доверяем.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.bot import texts
from app.bot.parsing import MAX_RANGE_DAYS, ParseError, check_ranges, days_between
from app.channels.max.adapter import mini_app_link
from app.db import get_session
from app.models import Channel, Master
from app.runtime import runtime
from app.services import masters as ms

router = APIRouter(prefix="/api/cabinet", tags=["cabinet"])
Session = Annotated[AsyncSession, Depends(get_session)]


async def current_master(user: CurrentUser, session: Session) -> Master:
    master = await ms.get_master_by_account(session, user.channel, user.external_user_id)
    if master is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Кабинет доступен только мастеру. Зарегистрируйтесь в боте.")
    return master


CurrentMaster = Annotated[Master, Depends(current_master)]


# ---------- схемы ----------

class Interval(BaseModel):
    start: dt.time
    end: dt.time


class DaySchedule(BaseModel):
    weekday: int = Field(ge=0, le=6)  # 0 — понедельник
    intervals: list[Interval] = Field(default_factory=list, max_length=6)


class WeeklySchedule(BaseModel):
    days: list[DaySchedule] = Field(max_length=7)

    @field_validator("days")
    @classmethod
    def _unique(cls, v: list[DaySchedule]) -> list[DaySchedule]:
        if len({d.weekday for d in v}) != len(v):
            raise ValueError("день недели указан дважды")
        return v


class Settings(BaseModel):
    min_lead_min: int
    horizon_days: int
    one_booking_per_day: bool
    timezone: str


class SettingsPatch(BaseModel):
    min_lead_min: int | None = None
    horizon_days: int | None = None
    one_booking_per_day: bool | None = None
    timezone: str | None = None


class Override(BaseModel):
    date: dt.date
    intervals: list[Interval]  # пусто — выходной


class OverridesPut(BaseModel):
    dates: list[dt.date] = Field(min_length=1, max_length=MAX_RANGE_DAYS)
    intervals: list[Interval] = Field(default_factory=list, max_length=6)


class ConflictOut(BaseModel):
    starts_at: dt.datetime
    service_name: str
    client_name: str
    phone: str | None


class CabinetOut(BaseModel):
    slug: str
    name: str
    client_link: str | None
    settings: Settings
    schedule: WeeklySchedule
    timezones: list[dict[str, str]]
    lead_options: list[dict]
    horizon_options: list[int]


# ---------- помощники ----------

def _ranges(intervals: list[Interval], where: str = "") -> list[tuple[dt.time, dt.time]]:
    try:
        return check_ranges([(i.start, i.end) for i in intervals], where)
    except ParseError as e:
        raise HTTPException(422, str(e)) from e


def _weekly_out(weekly) -> WeeklySchedule:
    return WeeklySchedule(days=[
        DaySchedule(weekday=d, intervals=[Interval(start=s, end=e) for s, e in weekly.get(d, [])])
        for d in range(7)
    ])


def _settings_out(m: Master) -> Settings:
    return Settings(min_lead_min=m.min_lead_min, horizon_days=m.horizon_days,
                    one_booking_per_day=m.one_booking_per_day, timezone=m.timezone)


def _client_link(master: Master, channel: Channel) -> str | None:
    username = runtime.bot_usernames.get(Channel.MAX)
    return mini_app_link(username, master.slug) if username else None


# ---------- эндпоинты ----------

@router.get("/me", response_model=CabinetOut)
async def me(master: CurrentMaster, user: CurrentUser, session: Session) -> CabinetOut:
    weekly = await ms.get_weekly_schedule(session, master.id)
    return CabinetOut(
        slug=master.slug,
        name=master.name,
        client_link=_client_link(master, user.channel),
        settings=_settings_out(master),
        schedule=_weekly_out(weekly),
        timezones=[{"id": tz, "title": title} for title, tz in texts.TIMEZONES],
        lead_options=[{"minutes": v, "title": t} for v, t in texts.LEAD_OPTIONS],
        horizon_options=texts.HORIZON_OPTIONS,
    )


@router.put("/schedule", response_model=WeeklySchedule)
async def put_schedule(body: WeeklySchedule, master: CurrentMaster, session: Session):
    titles = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    weekly = {
        d.weekday: _ranges(d.intervals, f"{titles[d.weekday]}: ")
        for d in body.days if d.intervals
    }
    if not weekly:
        raise HTTPException(422, "Нужен хотя бы один рабочий день — иначе клиенты не смогут записаться")
    await ms.set_weekly_schedule(session, master, weekly)
    await session.commit()
    return _weekly_out(weekly)


@router.patch("/settings", response_model=Settings)
async def patch_settings(body: SettingsPatch, master: CurrentMaster, session: Session):
    fields = body.model_dump(exclude_none=True)
    if "min_lead_min" in fields and fields["min_lead_min"] not in {v for v, _ in texts.LEAD_OPTIONS}:
        raise HTTPException(422, "Недопустимое значение «минимум до визита»")
    if "horizon_days" in fields and fields["horizon_days"] not in texts.HORIZON_OPTIONS:
        raise HTTPException(422, "Недопустимый горизонт записи")
    if "timezone" in fields and fields["timezone"] not in texts.TIMEZONE_IDS:
        raise HTTPException(422, "Недопустимый часовой пояс")
    await ms.update_settings(session, master, **fields)
    await session.commit()
    return _settings_out(master)


def _today(master: Master) -> dt.date:
    return dt.datetime.now(ZoneInfo(master.timezone)).date()


@router.get("/overrides", response_model=list[Override])
async def get_overrides(master: CurrentMaster, session: Session):
    overrides = await ms.upcoming_overrides(session, master.id, _today(master))
    return [
        Override(date=d, intervals=[Interval(start=s, end=e) for s, e in r])
        for d, r in sorted(overrides.items())
    ]


@router.put("/overrides", response_model=list[ConflictOut])
async def put_overrides(body: OverridesPut, master: CurrentMaster, session: Session):
    """Задать одинаковые часы (или выходной) на набор дат. Возвращает записи на эти даты."""
    today = _today(master)
    if min(body.dates) < today:
        raise HTTPException(422, "Нельзя менять прошедшие даты")
    ranges = _ranges(body.intervals)
    await ms.set_overrides(session, master, {d: list(ranges) for d in set(body.dates)})
    conflicts = await ms.appointments_on_days(session, master, sorted(set(body.dates)))
    await session.commit()
    tz = ZoneInfo(master.timezone)
    return [
        ConflictOut(starts_at=a.starts_at.astimezone(tz), service_name=a.service_name,
                    client_name=c.name, phone=c.phone)
        for a, c in conflicts
    ]


@router.delete("/overrides", status_code=status.HTTP_204_NO_CONTENT)
async def delete_overrides(
    master: CurrentMaster,
    session: Session,
    start: dt.date,
    end: Annotated[dt.date | None, Query()] = None,
) -> None:
    end = end or start
    if end < start or len(days_between(start, end)) > MAX_RANGE_DAYS:
        raise HTTPException(422, "Неверный период")
    await ms.delete_overrides(session, master, start, end)
    await session.commit()
