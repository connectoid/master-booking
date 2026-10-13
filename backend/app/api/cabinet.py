"""API кабинета мастера (Mini App): график, особые дни, настройки.

Доступ — только мастеру: пользователь из подписанного initData должен быть привязан
к мастеру в master_accounts. Параметрам из адреса не доверяем.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.bot import texts
from app.bot.parsing import MAX_RANGE_DAYS, ParseError, check_ranges, days_between
from app.channels.max.adapter import mini_app_link
from app import notify
from app.db import get_session
from app.domain.phone import normalize_phone
from app.models import Channel, Master
from app.runtime import runtime
from app.services import booking
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
    name: str
    min_lead_min: int
    horizon_days: int
    one_booking_per_day: bool
    timezone: str
    slot_step_min: int
    buffer_min: int


class SettingsPatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    min_lead_min: int | None = None
    horizon_days: int | None = None
    one_booking_per_day: bool | None = None
    timezone: str | None = None
    slot_step_min: int | None = None
    buffer_min: int | None = None


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
    today: dt.date      # сегодня по часовому поясу мастера
    utc_offset: str     # «+12:00» — для времени, которое мастер вводит вручную
    client_link: str | None
    settings: Settings
    schedule: WeeklySchedule
    timezones: list[dict[str, str]]
    lead_options: list[dict]
    horizon_options: list[int]
    step_options: list[int]
    buffer_options: list[int]


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
    return Settings(name=m.name, min_lead_min=m.min_lead_min, horizon_days=m.horizon_days,
                    one_booking_per_day=m.one_booking_per_day, timezone=m.timezone,
                    slot_step_min=m.slot_step_min, buffer_min=m.buffer_min)


def _client_link(master: Master, channel: Channel) -> str | None:
    username = runtime.bot_usernames.get(Channel.MAX)
    return mini_app_link(username, master.slug) if username else None


# ---------- эндпоинты ----------

@router.get("/me", response_model=CabinetOut)
async def me(master: CurrentMaster, user: CurrentUser, session: Session) -> CabinetOut:
    weekly = await ms.get_weekly_schedule(session, master.id)
    now_local = dt.datetime.now(ZoneInfo(master.timezone))
    offset = now_local.strftime("%z")
    return CabinetOut(
        slug=master.slug,
        name=master.name,
        today=now_local.date(),
        utc_offset=f"{offset[:3]}:{offset[3:]}",
        client_link=_client_link(master, user.channel),
        settings=_settings_out(master),
        schedule=_weekly_out(weekly),
        timezones=[{"id": tz, "title": title} for title, tz in texts.TIMEZONES],
        lead_options=[{"minutes": v, "title": t} for v, t in texts.LEAD_OPTIONS],
        horizon_options=texts.HORIZON_OPTIONS,
        step_options=texts.STEP_OPTIONS,
        buffer_options=texts.BUFFER_OPTIONS,
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
    if "slot_step_min" in fields and fields["slot_step_min"] not in texts.STEP_OPTIONS:
        raise HTTPException(422, "Недопустимый шаг времени")
    if "buffer_min" in fields and fields["buffer_min"] not in texts.BUFFER_OPTIONS:
        raise HTTPException(422, "Недопустимая пауза между клиентами")
    if "name" in fields:
        fields["name"] = " ".join(fields["name"].split())
        if len(fields["name"]) < 2:
            raise HTTPException(422, "Имя должно быть не короче 2 символов")
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



# ---------- записи ----------

class ClientOut(BaseModel):
    id: int
    name: str
    phone: str | None


class CabinetAppointment(BaseModel):
    id: int
    starts_at: dt.datetime
    ends_at: dt.datetime
    service_name: str
    price: int | None
    source: str
    comment: str | None
    client: ClientOut


class ServiceOut(BaseModel):
    id: int
    name: str
    duration_min: int
    price: int | None
    is_active: bool


def _service_out(s) -> ServiceOut:
    return ServiceOut(id=s.id, name=s.name, duration_min=s.duration_min, price=s.price,
                      is_active=s.is_active)


def _clean_service_name(v: str | None) -> str | None:
    if v is None:
        return v
    v = " ".join(v.split())
    if len(v) < 2:
        raise ValueError("Название — хотя бы 2 символа")
    return v


def _check_duration(v: int | None) -> int | None:
    if v is not None and v % 5:
        raise ValueError("Длительность — кратно 5 минутам")
    return v


class ServiceIn(BaseModel):
    name: str = Field(max_length=120)
    duration_min: int = Field(ge=5, le=720)
    price: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("name")
    @classmethod
    def _v_name(cls, v):
        return _clean_service_name(v)

    @field_validator("duration_min")
    @classmethod
    def _v_duration(cls, v):
        return _check_duration(v)


class ServicePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    duration_min: int | None = Field(default=None, ge=5, le=720)
    price: int | None = Field(default=None, ge=0, le=1_000_000)  # null = «по договорённости»
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _v_name(cls, v):
        return _clean_service_name(v)

    @field_validator("duration_min")
    @classmethod
    def _v_duration(cls, v):
        return _check_duration(v)


class ServiceOrder(BaseModel):
    ids: list[int] = Field(max_length=100)


class ManualCreate(BaseModel):
    service_id: int
    starts_at: AwareDatetime
    client_id: int | None = None
    client_name: str = Field(default="", max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return normalize_phone(v)

    @field_validator("client_name", "comment")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if v else v


_BOOKING_ERRORS = {
    booking.NotFound: 404, booking.SlotUnavailable: 422, booking.SlotTaken: 409,
    booking.Forbidden: 403, booking.DailyLimit: 409,
}


def _booking_http(e: booking.BookingError) -> HTTPException:
    return HTTPException(_BOOKING_ERRORS.get(type(e), 400), e.message)


def _appt_out(a, c, tz: ZoneInfo) -> CabinetAppointment:
    return CabinetAppointment(
        id=a.id, starts_at=a.starts_at.astimezone(tz), ends_at=a.ends_at.astimezone(tz),
        service_name=a.service_name, price=a.price, source=a.source, comment=a.comment,
        client=ClientOut(id=c.id, name=c.name, phone=c.phone),
    )


@router.get("/appointments", response_model=list[CabinetAppointment])
async def list_appointments(
    master: CurrentMaster,
    session: Session,
    date_from: dt.date | None = None,
    days: Annotated[int, Query(ge=1, le=92)] = 14,
):
    tz = ZoneInfo(master.timezone)
    start_day = date_from or _today(master)
    start = dt.datetime.combine(start_day, dt.time.min, tzinfo=tz)
    rows = await booking.master_appointments(session, master, start, start + dt.timedelta(days=days))
    return [_appt_out(a, c, tz) for a, c in rows]


@router.get("/services", response_model=list[ServiceOut])
async def list_services(master: CurrentMaster, session: Session, include_hidden: bool = False):
    """Без include_hidden — только услуги, на которые можно записать (для ручной записи)."""
    if include_hidden:
        services = await ms.all_services(session, master.id)
    else:
        services = await booking.list_services(session, master.id)
    return [_service_out(s) for s in services]


@router.post("/services", response_model=ServiceOut, status_code=201)
async def create_service(body: ServiceIn, master: CurrentMaster, session: Session):
    if len(await ms.all_services(session, master.id)) >= ms.MAX_SERVICES:
        raise HTTPException(422, f"Не больше {ms.MAX_SERVICES} услуг")
    service = await ms.create_service(session, master, name=body.name,
                                      duration_min=body.duration_min, price=body.price)
    await session.commit()
    return _service_out(service)


@router.patch("/services/{service_id}", response_model=ServiceOut)
async def patch_service(service_id: int, body: ServicePatch, master: CurrentMaster, session: Session):
    service = await ms.own_service(session, master, service_id)
    if service is None:
        raise HTTPException(404, "Услуга не найдена")
    fields = body.model_dump(exclude_unset=True)
    for key in ("name", "duration_min", "is_active"):
        if key in fields and fields[key] is None:
            raise HTTPException(422, "Пустое значение")
    ms.update_service(service, **fields)
    await session.commit()
    return _service_out(service)


@router.delete("/services/{service_id}", status_code=204)
async def delete_service(service_id: int, master: CurrentMaster, session: Session):
    if not await ms.delete_service(session, master, service_id):
        raise HTTPException(404, "Услуга не найдена")
    await session.commit()


@router.put("/services/order", response_model=list[ServiceOut])
async def reorder_services(body: ServiceOrder, master: CurrentMaster, session: Session):
    try:
        services = await ms.reorder_services(session, master, body.ids)
    except ValueError as err:
        raise HTTPException(409, str(err)) from err
    await session.commit()
    return [_service_out(s) for s in services]


@router.get("/free-slots", response_model=list[dt.datetime])
async def free_slots(master: CurrentMaster, session: Session, service_id: int, date: dt.date):
    """Свободное время по графику — подсказки для ручной записи (без «минимума до визита»)."""
    try:
        service = await booking.get_service(session, master, service_id)
    except booking.BookingError as e:
        raise _booking_http(e) from e
    result = await booking.compute_slots(
        session, master, service, date, date, booking.utcnow(), for_master=True
    )
    return result.get(date, [])


@router.get("/clients", response_model=list[ClientOut])
async def search_clients(master: CurrentMaster, session: Session, q: str = ""):
    return [ClientOut(id=c.id, name=c.name, phone=c.phone)
            for c in await booking.find_clients(session, master, q)]


@router.post("/appointments", response_model=CabinetAppointment, status_code=201)
async def create_manual(body: ManualCreate, master: CurrentMaster, session: Session):
    if body.client_id is None and not body.client_name:
        raise HTTPException(422, "Укажите имя клиента")
    tz = ZoneInfo(master.timezone)
    try:
        service = await booking.get_service(session, master, body.service_id)
        appt = await booking.create_manual_appointment(
            session, master, service, body.starts_at,
            client_name=body.client_name, phone=body.phone, client_id=body.client_id,
            comment=body.comment,
        )
    except booking.BookingError as e:
        raise _booking_http(e) from e
    client = await session.get(booking.Client, appt.client_id)
    return _appt_out(appt, client, tz)


@router.post("/appointments/{appointment_id}/cancel", status_code=204)
async def cancel(
    appointment_id: int, master: CurrentMaster, session: Session, background: BackgroundTasks
) -> None:
    try:
        await booking.cancel_appointment_by_master(session, master, appointment_id)
    except booking.BookingError as e:
        raise _booking_http(e) from e
    if runtime.messengers:
        await notify.appointment_cancelled_by_master(session, appointment_id)
