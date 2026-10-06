"""ORM-модели. Схема задаётся миграциями (migrations/sql/*.sql); модели должны ей соответствовать.

Ограничения CHECK и EXCLUDE живут в SQL, здесь только то, что нужно ORM.
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Channel(enum.StrEnum):
    MAX = "max"
    TELEGRAM = "telegram"
    WEB = "web"


class AppointmentSource(enum.StrEnum):
    MAX = "max"
    TELEGRAM = "telegram"
    WEB = "web"
    MANUAL = "manual"  # мастер добавил сам (клиент позвонил)


class AppointmentStatus(enum.StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


ACTIVE_STATUSES = (AppointmentStatus.PENDING, AppointmentStatus.CONFIRMED)


class ReminderKind(enum.StrEnum):
    DAY_BEFORE = "day_before"
    TWO_HOURS = "two_hours"


class ReminderStatus(enum.StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _now_default():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Base(DeclarativeBase):
    pass


class Master(Base):
    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), server_default="Europe/Moscow")
    slot_step_min: Mapped[int] = mapped_column(SmallInteger, server_default="15")
    min_lead_min: Mapped[int] = mapped_column(Integer, server_default="60")
    horizon_days: Mapped[int] = mapped_column(SmallInteger, server_default="30")
    buffer_min: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    trial_ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    paid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_default()

    accounts: Mapped[list[MasterAccount]] = relationship(back_populates="master")
    services: Mapped[list[Service]] = relationship(
        back_populates="master", order_by="Service.position"
    )
    working_intervals: Mapped[list[WorkingInterval]] = relationship(back_populates="master")


class MasterAccount(Base):
    __tablename__ = "master_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    channel: Mapped[Channel] = mapped_column(String(16))
    external_user_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = _now_default()

    master: Mapped[Master] = relationship(back_populates="accounts")


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    duration_min: Mapped[int] = mapped_column(SmallInteger)
    price: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    position: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    created_at: Mapped[dt.datetime] = _now_default()

    master: Mapped[Master] = relationship(back_populates="services")


class WorkingInterval(Base):
    """Недельный график; weekday 0 = понедельник. Время — локальное время мастера."""

    __tablename__ = "working_intervals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[dt.time] = mapped_column(Time)
    end_time: Mapped[dt.time] = mapped_column(Time)

    master: Mapped[Master] = relationship(back_populates="working_intervals")


class ScheduleOverride(Base):
    """Исключение на дату: заменяет недельный график. start/end = NULL — выходной."""

    __tablename__ = "schedule_overrides"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    day: Mapped[dt.date] = mapped_column(Date)
    start_time: Mapped[dt.time | None] = mapped_column(Time)
    end_time: Mapped[dt.time | None] = mapped_column(Time)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_default()

    contacts: Mapped[list[ClientContact]] = relationship(back_populates="client")


class ClientContact(Base):
    __tablename__ = "client_contacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    channel: Mapped[Channel] = mapped_column(String(16))
    external_user_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = _now_default()

    client: Mapped[Client] = relationship(back_populates="contacts")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    service_name: Mapped[str] = mapped_column(String(120))
    price: Mapped[int | None] = mapped_column(Integer)
    starts_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[AppointmentStatus] = mapped_column(
        String(16), server_default=AppointmentStatus.CONFIRMED.value
    )
    source: Mapped[AppointmentSource] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _now_default()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client: Mapped[Client] = relationship()
    reminders: Mapped[list[Reminder]] = relationship(back_populates="appointment")


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE")
    )
    kind: Mapped[ReminderKind] = mapped_column(String(16))
    send_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ReminderStatus] = mapped_column(
        String(16), server_default=ReminderStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    appointment: Mapped[Appointment] = relationship(back_populates="reminders")
