"""Фоновая отправка напоминаний клиентам.

Каждые N секунд выбирает созревшие напоминания (FOR UPDATE SKIP LOCKED — безопасно
при нескольких процессах) и отправляет их в мессенджер клиента.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from sqlalchemy import select

from app.models import (
    ACTIVE_STATUSES,
    Appointment,
    Channel,
    ClientContact,
    Master,
    Reminder,
    ReminderStatus,
)
from app.notify import client_keyboard, reminder_text
from app.runtime import runtime

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BATCH = 50
# «За сутки» не шлём, если до визита осталось меньше этого (сервер был недоступен) —
# хватит напоминания «за 2 часа»
DAY_BEFORE_MIN_LEAD = dt.timedelta(hours=3)


async def process_due(now: dt.datetime | None = None) -> int:
    """Отправить созревшие напоминания. Возвращает число отправленных."""
    if runtime.session_factory is None:
        return 0
    now = now or dt.datetime.now(dt.UTC)
    sent = 0
    async with runtime.session_factory() as session:
        reminders = list(await session.scalars(
            select(Reminder)
            .where(Reminder.status == ReminderStatus.PENDING.value, Reminder.send_at <= now)
            .order_by(Reminder.send_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        ))
        for r in reminders:
            appt = await session.get(Appointment, r.appointment_id)
            if (
                appt is None
                or appt.status not in [s.value for s in ACTIVE_STATUSES]
                or appt.starts_at <= now
                or (r.kind == "day_before" and appt.starts_at - now < DAY_BEFORE_MIN_LEAD)
            ):
                r.status = ReminderStatus.CANCELLED.value
                continue
            # первый контакт, в который можно написать (у записавшихся из браузера
            # первым идёт контакт web — он без мессенджера)
            contact, messenger = None, None
            for c in await session.scalars(
                select(ClientContact)
                .where(ClientContact.client_id == appt.client_id)
                .order_by(ClientContact.id)
            ):
                messenger = runtime.messengers.get(Channel(c.channel))
                if messenger is not None:
                    contact = c
                    break
            if messenger is None:
                r.status = ReminderStatus.FAILED.value
                r.last_error = "у клиента нет мессенджера для напоминаний"
                continue
            master = await session.get(Master, appt.master_id)
            try:
                await messenger.send(
                    contact.external_user_id,
                    reminder_text(r.kind, appt, master),
                    client_keyboard(appt.id),
                )
            except Exception as e:  # сеть, лимиты API — попробуем в следующий проход
                r.attempts += 1
                r.last_error = str(e)[:500]
                if r.attempts >= MAX_ATTEMPTS:
                    r.status = ReminderStatus.FAILED.value
                log.warning("Напоминание %s не отправлено: %s", r.id, e)
                continue
            r.status = ReminderStatus.SENT.value
            r.sent_at = now
            sent += 1
        await session.commit()
    return sent


async def reminder_loop(interval_sec: int = 30) -> None:
    while True:
        try:
            await process_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка в цикле напоминаний")
        await asyncio.sleep(interval_sec)
