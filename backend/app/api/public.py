"""API для клиента (Mini App): просмотр услуг и слотов — публично, запись — с входом через мессенджер."""
import datetime as dt
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.api.schemas import (
    AppointmentCreate,
    AppointmentOut,
    MasterOut,
    ServiceOut,
    SlotsDay,
    SlotsOut,
)
from app import notify
from app.db import get_session
from app.models import Appointment
from app.runtime import runtime
from app.services import booking

router = APIRouter(prefix="/api", tags=["client"])
Session = Annotated[AsyncSession, Depends(get_session)]

_ERROR_STATUS = {
    booking.NotFound: status.HTTP_404_NOT_FOUND,
    booking.SlotUnavailable: 422,
    booking.SlotTaken: status.HTTP_409_CONFLICT,
    booking.Forbidden: status.HTTP_403_FORBIDDEN,
    booking.DailyLimit: status.HTTP_409_CONFLICT,
}


def _http(e: booking.BookingError) -> HTTPException:
    return HTTPException(_ERROR_STATUS.get(type(e), status.HTTP_400_BAD_REQUEST), e.message)


def _appointment_out(a: Appointment, tz: ZoneInfo) -> AppointmentOut:
    return AppointmentOut(
        id=a.id,
        service_name=a.service_name,
        price=a.price,
        starts_at=a.starts_at.astimezone(tz),
        ends_at=a.ends_at.astimezone(tz),
        status=a.status,
    )


@router.get("/masters/{slug}", response_model=MasterOut)
async def get_master(slug: str, session: Session) -> MasterOut:
    try:
        master = await booking.get_master(session, slug)
    except booking.BookingError as e:
        raise _http(e) from e
    services = await booking.list_services(session, master.id)
    return MasterOut(
        slug=master.slug,
        name=master.name,
        timezone=master.timezone,
        one_booking_per_day=master.one_booking_per_day,
        services=[
            ServiceOut(id=s.id, name=s.name, duration_min=s.duration_min, price=s.price)
            for s in services
        ],
    )


@router.get("/masters/{slug}/slots", response_model=SlotsOut)
async def get_slots(
    slug: str,
    session: Session,
    service_id: int,
    date_from: dt.date | None = None,
    days: Annotated[int, Query(ge=1, le=booking.MAX_DAYS_PER_REQUEST)] = 7,
) -> SlotsOut:
    now = booking.utcnow()
    try:
        master = await booking.get_master(session, slug)
        service = await booking.get_service(session, master, service_id)
    except booking.BookingError as e:
        raise _http(e) from e
    start = date_from or booking.master_today(master, now)
    result = await booking.compute_slots(
        session, master, service, start, start + dt.timedelta(days=days - 1), now
    )
    return SlotsOut(
        timezone=master.timezone,
        service_id=service.id,
        days=[SlotsDay(date=d, slots=s) for d, s in sorted(result.items())],
    )


@router.post(
    "/masters/{slug}/appointments",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_appointment(
    slug: str,
    body: AppointmentCreate,
    user: CurrentUser,
    session: Session,
    background: BackgroundTasks,
) -> AppointmentOut:
    try:
        master = await booking.get_master(session, slug)
        service = await booking.get_service(session, master, body.service_id)
        appointment = await booking.create_appointment(
            session, master, service, body.starts_at, user,
            client_name=body.client_name, phone=body.phone, comment=body.comment,
        )
    except booking.BookingError as e:
        raise _http(e) from e
    background.add_task(notify.appointment_created, appointment.id)
    return _appointment_out(appointment, ZoneInfo(master.timezone))


@router.get("/masters/{slug}/appointments/my", response_model=list[AppointmentOut])
async def my_appointments(slug: str, user: CurrentUser, session: Session) -> list[AppointmentOut]:
    try:
        master = await booking.get_master(session, slug)
    except booking.BookingError as e:
        raise _http(e) from e
    tz = ZoneInfo(master.timezone)
    items = await booking.list_client_appointments(session, master, user)
    return [_appointment_out(a, tz) for a in items]


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel_appointment(
    appointment_id: int, user: CurrentUser, session: Session
) -> AppointmentOut:
    try:
        appointment = await booking.cancel_appointment_by_client(session, appointment_id, user)
    except booking.BookingError as e:
        raise _http(e) from e
    if runtime.messengers:
        await notify.appointment_cancelled_by_client(session, appointment.id)
    master = await session.get(booking.Master, appointment.master_id)
    return _appointment_out(appointment, ZoneInfo(master.timezone))
