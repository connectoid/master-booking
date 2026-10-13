import datetime as dt
import re

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class ServiceOut(BaseModel):
    id: int
    name: str
    duration_min: int
    price: int | None


class MasterOut(BaseModel):
    slug: str
    name: str
    timezone: str
    one_booking_per_day: bool = False
    horizon_days: int = 30
    services: list[ServiceOut]


class SlotsDay(BaseModel):
    date: dt.date
    slots: list[dt.datetime]  # ISO 8601 с поясом мастера


class SlotsOut(BaseModel):
    timezone: str
    service_id: int
    days: list[SlotsDay]


class AppointmentCreate(BaseModel):
    service_id: int
    starts_at: AwareDatetime
    client_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("client_name", "comment")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        """Принимаем «8 (914) 123-45-67», «+7 914 1234567» и т.п.; храним +7XXXXXXXXXX."""
        if not v or not v.strip():
            return None
        digits = re.sub(r"\D", "", v)
        if len(digits) == 11 and digits[0] in "78":
            return "+7" + digits[1:]
        if len(digits) == 10:
            return "+7" + digits
        raise ValueError("Неверный номер телефона")


class AppointmentOut(BaseModel):
    id: int
    service_name: str
    price: int | None
    starts_at: dt.datetime
    ends_at: dt.datetime
    status: str
